"""Customer-facing upload pre-check: are these photos good enough to build
a correct video ad from?

Runs BEFORE a project exists and consumes nothing durable: deterministic
checks (resolution, aspect, duplicates), a segmentation probe (can we isolate
the product? how messy is the matte?), and — when an OpenAI key is configured —
ONE structured vision call over the whole batch that catches what pixels alone
cannot: marketing composites, text overlays, multiple products in one frame,
watermarks. Output is plain-language verdicts the create screen can show the
customer photo by photo."""
import base64
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app import product_references, segmentation
from app.providers.openai_client import structured_call

Verdict = Literal["good", "usable", "replace"]
_RANK = {"good": 0, "usable": 1, "replace": 2}


class PhotoIssue(BaseModel):
    code: str
    message: str                  # what is wrong, customer language
    tip: str = ""                 # how to fix it


class PhotoVerdict(BaseModel):
    filename: str
    verdict: Verdict = "good"
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    cutout_ok: bool = False
    issues: list[PhotoIssue] = Field(default_factory=list)

    def worsen(self, verdict: Verdict) -> None:
        if _RANK[verdict] > _RANK[self.verdict]:
            self.verdict = verdict


class PrecheckResult(BaseModel):
    ok_to_proceed: bool
    summary: str
    photos: list[PhotoVerdict]
    ai_checked: bool = False
    cost_cents: int = 0


class _AIPhotoFinding(BaseModel):
    photo_number: int = Field(ge=1)
    clean_single_product: bool
    issues: list[Literal[
        "text_overlay", "multiple_products", "collage_or_composite",
        "watermark", "busy_background", "blurry", "product_cut_off",
        "not_a_product_photo",
    ]] = Field(default_factory=list)
    tip: str = ""


class _AIAssessment(BaseModel):
    findings: list[_AIPhotoFinding]


_AI_ISSUE_TEXT = {
    "text_overlay": "The photo has marketing text on it",
    "multiple_products": "More than one product appears in the frame",
    "collage_or_composite": "This looks like a collage or listing graphic, not a single photo",
    "watermark": "The photo has a watermark",
    "busy_background": "The background is busy",
    "blurry": "The photo looks blurry",
    "product_cut_off": "Part of the product is cut off",
    "not_a_product_photo": "The product is not clearly the subject of this photo",
}
# issues that mean the generated ad would copy the flaw into every scene
_AI_REPLACE_ISSUES = {"text_overlay", "multiple_products", "collage_or_composite",
                      "watermark", "not_a_product_photo"}

AI_PROMPT = (
    "You screen product photos for an AI video-ad builder. The builder needs "
    "clean photos of ONE product per frame on a plain background — they become "
    "the visual ground truth for every generated scene, so text overlays, "
    "collages, watermarks, or multiple products would leak into the ad.\n"
    "For each numbered photo report: clean_single_product, the issues that "
    "apply, and one short friendly tip (e.g. 'Retake on a plain table without "
    "the text banner'). An empty issues list means the photo is clean."
)


def _image_block(raw: bytes, mime: str) -> dict:
    b64 = base64.b64encode(raw).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def ai_assess(photos: list[tuple[str, bytes, str]]) -> tuple[_AIAssessment, int]:
    """One structured vision call for the whole batch. (name, raw, mime)."""
    content: list[dict] = [{"type": "text", "text": AI_PROMPT}]
    for index, (_, raw, mime) in enumerate(photos, start=1):
        content.append({"type": "text", "text": f"Photo {index}:"})
        content.append(_image_block(raw, mime))
    return structured_call(_AIAssessment, [{"role": "user", "content": content}])


def _deterministic_checks(verdict: PhotoVerdict, raw: bytes,
                          seen_hashes: set[str]) -> None:
    try:
        width, height = product_references.image_dimensions(raw)
    except Exception:
        verdict.worsen("replace")
        verdict.issues.append(PhotoIssue(
            code="unreadable",
            message="We could not open this image",
            tip="Upload a standard JPG, PNG, or WebP photo.",
        ))
        return
    digest = product_references.content_hash(raw)
    if digest in seen_hashes:
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="duplicate",
            message="This photo is a duplicate of another upload",
            tip="Replace it with a different angle — every new angle sharpens identity.",
        ))
    seen_hashes.add(digest)
    short_edge = min(width, height)
    if short_edge < 512:
        verdict.worsen("replace")
        verdict.issues.append(PhotoIssue(
            code="too_small",
            message=f"The photo is too small ({width}×{height})",
            tip="Use a photo at least 1024 pixels on the short side.",
        ))
    elif short_edge < 1024:
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="low_resolution",
            message=f"The photo is a little small ({width}×{height})",
            tip="1024 pixels or more on the short side gives sharper results.",
        ))
    aspect = max(width / height, height / width)
    if aspect > 3.0:
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="extreme_crop",
            message="The photo is an extreme crop",
            tip="Use a photo that shows the whole product with some space around it.",
        ))
    verdict.quality_score = product_references.reference_quality(raw)


def _segmentation_probe(verdict: PhotoVerdict, raw: bytes) -> None:
    try:
        cutout, warning = segmentation.segment(raw)
    except Exception:
        cutout, warning = None, None
    if cutout is not None:
        verdict.cutout_ok = True
        alpha = segmentation.alpha_coverage(cutout)
        verdict.quality_score = product_references.reference_quality(cutout, alpha)
    code = warning.code if warning else None
    if code == "segmentation_low_coverage":
        verdict.worsen("replace")
        verdict.issues.append(PhotoIssue(
            code="product_too_small",
            message="The product fills too little of the frame",
            tip="Move closer so the product fills most of the photo.",
        ))
    elif code == "segmentation_high_coverage":
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="no_clear_background",
            message="We could not separate the product from the background",
            tip="Retake on a plain, contrasting background.",
        ))
    elif code == "segmentation_fragmented":
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="busy_cutout",
            message="The background is busy around the product",
            tip="A plain background helps us keep fine details like comb teeth exact.",
        ))
    elif code == "segmentation_failed" and cutout is None:
        # recoverable: the original still works as a reference image
        verdict.worsen("usable")
        verdict.issues.append(PhotoIssue(
            code="cutout_unavailable",
            message="We could not build a cutout from this photo",
            tip="It can still be used as a reference; a plain background works best.",
        ))


def _summary(photos: list[PhotoVerdict], ok: bool) -> str:
    good = sum(1 for p in photos if p.verdict == "good")
    usable = sum(1 for p in photos if p.verdict == "usable")
    replace = sum(1 for p in photos if p.verdict == "replace")
    if not ok:
        return ("These photos will not produce a correct ad yet — please replace "
                "the flagged ones before continuing.")
    parts = []
    if good:
        parts.append(f"{good} photo{'s' if good != 1 else ''} look{'s' if good == 1 else ''} great")
    if usable:
        parts.append(f"{usable} usable with small improvements")
    if replace:
        parts.append(f"{replace} should be replaced")
    return "; ".join(parts) + "." if parts else "Add product photos to begin."


def run(photos: list[tuple[str, bytes, str]],
        use_ai: Optional[bool] = None) -> PrecheckResult:
    """(filename, raw, mime) per photo. use_ai=None -> auto (key present)."""
    verdicts: list[PhotoVerdict] = []
    seen: set[str] = set()
    for name, raw, _ in photos:
        verdict = PhotoVerdict(filename=name)
        _deterministic_checks(verdict, raw, seen)
        if verdict.verdict != "replace" or verdict.issues[0].code != "unreadable":
            _segmentation_probe(verdict, raw)
        verdicts.append(verdict)

    ai_checked = False
    cost = 0
    if use_ai is None:
        use_ai = bool(os.environ.get("OPENAI_API_KEY"))
    readable = [(i, p) for i, p in enumerate(verdicts)
                if not any(issue.code == "unreadable" for issue in p.issues)]
    if use_ai and readable:
        try:
            assessment, cost = ai_assess([photos[i] for i, _ in readable])
            ai_checked = True
            by_number = {f.photo_number: f for f in assessment.findings}
            for order, (_, verdict) in enumerate(readable, start=1):
                finding = by_number.get(order)
                if finding is None or finding.clean_single_product:
                    continue
                for issue in finding.issues:
                    verdict.worsen("replace" if issue in _AI_REPLACE_ISSUES else "usable")
                    verdict.issues.append(PhotoIssue(
                        code=f"ai_{issue}",
                        message=_AI_ISSUE_TEXT.get(issue, issue.replace("_", " ")),
                        tip=finding.tip,
                    ))
        except Exception:
            ai_checked = False   # AI screen is best-effort; deterministic verdicts stand

    ok = any(p.verdict != "replace" for p in verdicts) if verdicts else False
    return PrecheckResult(
        ok_to_proceed=ok,
        summary=_summary(verdicts, ok),
        photos=verdicts,
        ai_checked=ai_checked,
        cost_cents=cost,
    )
