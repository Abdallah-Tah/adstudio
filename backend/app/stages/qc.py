"""Stage 7: automated QC on generated clips (claude-real-video + Claude vision).

Per clip: crv extracts the scene-change keyframes locally, then ONE Claude
vision call judges them against the product reference images + scene intent.
Structured output validated against QCVerdict (hard rule: never free-text).
Spend is metered to CostLedger.qc.
"""
import base64
import io
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image
from pydantic import BaseModel

from app.pricing import ANTHROPIC_TOKEN_PRICES
from app.schema import Scene

QC_MODEL = "claude-haiku-4-5"     # cheap bounded vision classification per clip
MAX_KEYFRAMES = 8                 # cap tokens per verdict
MAX_REFERENCES = 2


class QCVerdict(BaseModel):
    identity_ok: bool             # product identical to the references
    artifacts: bool               # warping / morphing / extra parts / glitches
    frame_drift: bool = False     # product changes between keyframes over time
    caption_legible: Optional[bool] = None  # null until captions are burned in
    notes: str

    @property
    def notes_report_failure(self) -> bool:
        """Fail closed when a model's verdict fields contradict its notes."""
        note = self.notes.lower()
        negative = (
            "no visible warping", "no warping", "no morphing", "no frame drift",
            "without generation glitches", "no generation glitches",
            "without glitches", "no visible glitches",
        )
        if any(phrase in note for phrase in negative):
            return False
        patterns = (
            r"significant (?:frame )?drift",
            r"(?:obvious|visible) (?:frame )?(?:drift|morphing|warping)",
            r"(?:morphing|warping) artifacts?",
            r"inconsistent rendering",
            r"loss of product identity",
            r"product identity (?:is )?inconsistent",
            r"generation glitches? that compromise",
        )
        return any(re.search(pattern, note) for pattern in patterns)

    @property
    def passed(self) -> bool:
        return (
            self.identity_ok
            and not self.artifacts
            and not self.frame_drift
            and not self.notes_report_failure
        )


def qc_cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    inp, out = ANTHROPIC_TOKEN_PRICES[model]
    dollars = input_tokens * inp / 1_000_000 + output_tokens * out / 1_000_000
    return math.ceil(dollars * 100)


def extract_keyframes(clip_bytes: bytes) -> list[bytes]:
    """crv: scene-change keyframes, deduplicated, transcription off. Local."""
    import claude_real_video as crv

    with tempfile.TemporaryDirectory(prefix="adstudio-qc-") as tmp:
        clip_path = Path(tmp) / "clip.mp4"
        clip_path.write_bytes(clip_bytes)
        result = crv.process(str(clip_path), str(Path(tmp) / "out"),
                             do_transcribe=False, max_frames=MAX_KEYFRAMES)
        frames = sorted(Path(result.frames_dir).glob("*.jpg"))
        return [f.read_bytes() for f in frames[:MAX_KEYFRAMES]]


def _image_block(data: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode(),
        },
    }


def _media_type(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
    except Exception:
        return "image/png"
    if fmt in {"jpeg", "jpg"}:
        return "image/jpeg"
    if fmt == "webp":
        return "image/webp"
    return "image/png"


def _client():
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=key)


def run_qc(
    clip_bytes: bytes,
    reference_pngs: list[bytes],
    scene: Scene,
    captions_burned: bool = False,
    start_frame_included: bool = False,
) -> tuple[QCVerdict, int]:
    """Returns (verdict, cost_cents). One vision call per clip.

    When `start_frame_included` is true, reference_pngs[0] is the QC-approved
    still the clip was generated from — the identity anchor the video must not
    drift away from."""
    keyframes = extract_keyframes(clip_bytes)
    refs = reference_pngs[:MAX_REFERENCES]

    anchor_note = (
        "The FIRST reference is the approved still this clip was generated "
        "from; the product in every keyframe must stay identical to it.\n"
        if start_frame_included and refs else ""
    )
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"You are the quality gate for a product video ad.\n"
            f"The first {len(refs)} image(s) are REFERENCE photos of the real "
            f"product. {anchor_note}"
            f"The remaining {len(keyframes)} images are keyframes IN TIME ORDER "
            "from a generated clip that is supposed to show:\n"
            f"- Action: {scene.action}\n"
            f"- Camera: {scene.camera}\n"
            f"- Lighting: {scene.lighting}\n\n"
            "Judge strictly:\n"
            "- identity_ok: the product in the keyframes is the SAME object as "
            "the references — same shape, colors, materials, markings. Any "
            "substitution, restyling, or invented parts means false.\n"
            "- artifacts: true if there is warping, morphing, flicker, extra "
            "limbs/parts, garbled text, or obvious generation glitches.\n"
            "- frame_drift: true if the product CHANGES ACROSS the keyframes "
            "over time — parts appearing or disappearing, attachment geometry, "
            "product length, chamber size, controls, logo, or proportions "
            "shifting between frames, even if each frame looks plausible "
            "alone.\n"
            + ("- caption_legible: are the burned-in captions readable?\n"
               if captions_burned else
               "- caption_legible: null (no captions in this clip).\n")
            + "- notes: one or two sentences explaining your verdict. The "
            "booleans and notes must agree: if your notes mention morphing, "
            "warping, a glitch, identity inconsistency, or frame drift, set "
            "artifacts=true or frame_drift=true."
        ),
    }]
    for ref in refs:
        content.append(_image_block(ref, _media_type(ref)))
    for frame in keyframes:
        content.append(_image_block(frame, "image/jpeg"))

    response = _client().messages.parse(
        model=QC_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
        output_format=QCVerdict,
    )
    verdict = response.parsed_output
    if verdict is None:
        raise RuntimeError("QC call returned no parsed verdict "
                           f"(stop_reason: {response.stop_reason})")
    cost = qc_cost_cents(QC_MODEL, response.usage.input_tokens,
                         response.usage.output_tokens)
    return verdict, cost
