"""Phase 3 video engine: Kling v3 Standard via fal.ai — SKELETON ONLY.

Gate 2 decision (2026-07-15):
    GO — Kling v3 Standard through fal.ai, audio disabled.
    fal-ai/kling-video/v3/standard/image-to-video, generate_audio=false

Implementation notes for Phase 3 (do NOT submit paid jobs before Gate items pass):
- The selected generated image is the start frame (image-to-video).
- Use fal's QUEUE submission + webhook (or queue-status polling) with
  idempotent completion handling. Never block a Celery task while fal renders.
- generate_audio stays False: VO + licensed music are produced by our own
  pipeline; native audio adds cost ($0.126/s vs $0.084/s) without value here.
- Pricing lives in app.pricing (versioned config), not here.
"""
from app.pricing import VIDEO_PRICES
from app.compiler.base import EngineCapabilities
from app.schema import AssetRef

VIDEO_MODEL = "fal-ai/kling-video/v3/standard/image-to-video"
GENERATE_AUDIO = False  # hard-off for v0.1


class KlingFalEngine:
    """VideoEngine implementation target for Phase 3."""

    capabilities = EngineCapabilities(
        max_duration_s=12.0,  # per-clip cap; scenes are ≤8s so one clip per scene
        ref_conditioning=True,
        cost_per_s=VIDEO_PRICES[VIDEO_MODEL]["per_second"],
    )

    def generate(self, image: AssetRef, prompt: str,
                 duration_s: float, seed: int | None) -> AssetRef:
        raise NotImplementedError(
            "Phase 3: queue-submit to fal + webhook completion. "
            "Blocked at Gate 2 conditional hold — no paid video jobs yet."
        )
