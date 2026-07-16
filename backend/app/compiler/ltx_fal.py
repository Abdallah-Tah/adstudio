"""Phase 3 video engine #2: LTX-Video 13B 0.9.8 distilled via fal.ai.

Added on user request as a selectable second engine (default stays Kling — the
Gate 2 primary). Select with the FAL-side env `VIDEO_ENGINE=ltx`.

The LTX-Video library (github.com/Lightricks/LTX-Video, pip `ltx-video`,
import `ltx_video`) is a CUDA/H100 model — it cannot run on this Pi, so we use
it through fal's hosted endpoint, which runs Lightricks' own pipeline. We still
adopt three conventions FROM their library:
  1. num_frames must satisfy (n - 1) % 8 == 0  (their VAE temporal stride)
  2. their recommended negative prompt
  3. `expand_prompt=True` = their "Automatic Prompt Enhancement" (enhance_prompt)

Facts verified against fal docs (2026-07-15):
- Endpoint: fal-ai/ltxv-13b-098-distilled/image-to-video, same queue.fal.run REST.
- Input: prompt (req), image_url (req), negative_prompt, resolution
  ("480p"|"720p"), aspect_ratio ("9:16"|"1:1"|"16:9"|"auto"), num_frames
  (default 121), frame_rate (default 24), seed, expand_prompt.
- Output: {"video": {"url": ...}, "prompt": ..., "seed": ...}.
- Pricing $0.02/generated second at 24fps → app.pricing.
"""
import hashlib
import math

from pydantic import BaseModel

from app.compiler.base import EngineCapabilities
from app.pricing import VIDEO_PRICES
from app.schema import Scene
from app.styles import STYLES

VIDEO_MODEL = "fal-ai/ltxv-13b-098-distilled/image-to-video"
FRAME_RATE = 24
RESOLUTION = "720p"
ASPECT_RATIO = "9:16"           # vertical ad
EXPAND_PROMPT = True            # LTX Automatic Prompt Enhancement (their library)

# LTX VAE temporal stride: valid num_frames are 8k+1. Clamp to the model's
# supported window (~1s..~10.7s at 24fps).
MIN_FRAMES, MAX_FRAMES = 25, 257

# LTX-Video's published recommended negative prompt, extended with the
# product-identity guards we use everywhere else.
NEGATIVE_PROMPT = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "morphing product, product changing shape, extra parts appearing, "
    "text, captions, watermark, logo"
)

CAPABILITIES = EngineCapabilities(
    max_duration_s=MAX_FRAMES / FRAME_RATE,
    ref_conditioning=True,       # selected image is the start frame
    cost_per_s=VIDEO_PRICES[VIDEO_MODEL]["per_second"],
)


class CompiledVideoPrompt(BaseModel):
    provider: str = "fal"
    model: str = VIDEO_MODEL
    prompt: str
    negative_prompt: str = NEGATIVE_PROMPT
    num_frames: int
    frame_rate: int = FRAME_RATE
    prompt_hash: str
    start_image_generation_id: str

    @property
    def billed_seconds(self) -> float:
        return self.num_frames / self.frame_rate


def frames_for_duration(duration_s: float, fps: int = FRAME_RATE) -> int:
    """Round the target frame count to the nearest valid 8k+1 (LTX convention)."""
    target = duration_s * fps
    k = round((target - 1) / 8)
    frames = 8 * k + 1
    return min(MAX_FRAMES, max(MIN_FRAMES, frames))


def video_cost_cents(duration_s: float) -> int:
    frames = frames_for_duration(duration_s)
    dollars = (frames / FRAME_RATE) * VIDEO_PRICES[VIDEO_MODEL]["per_second"]
    return math.ceil(dollars * 100)


def compile_video_prompt(scene: Scene, style_id: str) -> CompiledVideoPrompt:
    """Motion-only prompt (identity is carried by the start frame). LTX responds
    well to rich natural-language motion description, so this reads as prose."""
    style = STYLES[style_id]
    prompt = (
        f"The product from the first frame stays exactly the same — identical "
        f"shape, colors, materials, and markings — while the shot comes alive. "
        f"{scene.action}. Camera: {scene.camera}. Lighting: {scene.lighting}. "
        f"Motion feels {style['motion']}. Vertical 9:16 framing, smooth "
        f"photorealistic motion, no on-screen text or logos."
    )
    if scene.selected_image is None:
        raise ValueError(f"scene {scene.scene_id} has no selected image")
    return CompiledVideoPrompt(
        prompt=prompt,
        num_frames=frames_for_duration(scene.duration_s),
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        start_image_generation_id=scene.selected_image,
    )


def build_payload(compiled: CompiledVideoPrompt, start_image_uri: str) -> dict:
    """LTX's fal input schema (image_url + num_frames/frame_rate)."""
    return {
        "prompt": compiled.prompt,
        "negative_prompt": compiled.negative_prompt,
        "image_url": start_image_uri,
        "num_frames": compiled.num_frames,
        "frame_rate": compiled.frame_rate,
        "resolution": RESOLUTION,
        "aspect_ratio": ASPECT_RATIO,
        "expand_prompt": EXPAND_PROMPT,
    }
