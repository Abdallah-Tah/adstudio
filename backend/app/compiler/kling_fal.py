"""Phase 3 video target: Kling v3 Standard via fal.ai.

Gate 2 decision (2026-07-15):
    GO — Kling v3 Standard through fal.ai, audio disabled.
    fal-ai/kling-video/v3/standard/image-to-video, generate_audio=false

Facts verified against fal's docs (2026-07-15):
- Queue REST: POST https://queue.fal.run/<model>, `Authorization: Key $FAL_KEY`;
  response carries request_id + status_url + response_url for polling.
- Input: start_image_url (required), prompt, duration (3-15 s, default 5),
  generate_audio (default true — we send false), negative_prompt, cfg_scale.
- Output: {"video": {"url": ..., "content_type": "video/mp4", ...}}.
- generate_audio stays False: VO + licensed music are produced by our own
  pipeline; native audio adds cost ($0.126/s vs $0.084/s) without value here.
- Pricing lives in app.pricing (versioned config), not here.
"""
import hashlib
import math

from pydantic import BaseModel

from app.compiler.base import EngineCapabilities
from app.pricing import VIDEO_PRICES
from app.schema import Scene
from app.styles import STYLES

VIDEO_MODEL = "fal-ai/kling-video/v3/standard/image-to-video"
GENERATE_AUDIO = False           # hard-off for v0.1
MIN_BILLED_S, MAX_BILLED_S = 3, 15
NEGATIVE_PROMPT = (
    "blur, distortion, low quality, morphing product, product changing shape, "
    "extra parts appearing, text, captions, watermark, logo"
)

CAPABILITIES = EngineCapabilities(
    max_duration_s=float(MAX_BILLED_S),
    ref_conditioning=True,       # selected image is the start frame
    cost_per_s=VIDEO_PRICES[VIDEO_MODEL]["per_second"],
)


class CompiledVideoPrompt(BaseModel):
    provider: str = "fal"
    model: str = VIDEO_MODEL
    prompt: str
    negative_prompt: str = NEGATIVE_PROMPT
    billed_duration_s: int       # what Kling renders/bills; trimmed at render
    prompt_hash: str
    start_image_generation_id: str


def billed_duration(duration_s: float) -> int:
    """Kling bills whole seconds in [3, 15]; we trim to intent at render."""
    return min(MAX_BILLED_S, max(MIN_BILLED_S, math.ceil(duration_s)))


def video_cost_cents(duration_s: float) -> int:
    dollars = billed_duration(duration_s) * VIDEO_PRICES[VIDEO_MODEL]["per_second"]
    return math.ceil(dollars * 100)


def compile_video_prompt(scene: Scene, style_id: str) -> CompiledVideoPrompt:
    """Motion-only prompt: identity comes from the start frame, so the prompt
    never re-describes the product (same delta discipline as images)."""
    style = STYLES[style_id]
    prompt = (
        "Animate this exact scene. The product must stay exactly as shown in "
        "the start frame — same shape, colors, materials, markings.\n"
        f"Action: {scene.action}\n"
        f"Camera: {scene.camera}\n"
        f"Lighting: {scene.lighting}\n"
        f"Motion style: {style['motion']}\n"
        "Vertical 9:16. Smooth, realistic motion. No text, no logos, no people's "
        "faces in focus."
    )
    if scene.selected_image is None:
        raise ValueError(f"scene {scene.scene_id} has no selected image")
    return CompiledVideoPrompt(
        prompt=prompt,
        billed_duration_s=billed_duration(scene.duration_s),
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        start_image_generation_id=scene.selected_image,
    )


def build_payload(compiled: CompiledVideoPrompt, start_image_uri: str) -> dict:
    """Kling's fal input schema (start_image_url + whole-second duration)."""
    return {
        "prompt": compiled.prompt,
        "negative_prompt": compiled.negative_prompt,
        "start_image_url": start_image_uri,
        "duration": str(compiled.billed_duration_s),
        "generate_audio": GENERATE_AUDIO,
    }
