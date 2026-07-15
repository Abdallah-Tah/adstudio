"""rembg product cutouts on upload.

Cutout PNGs are stored as additional `reference` assets alongside the
originals. If the cutout is poor (alpha coverage < 5% or > 95%) we keep only
the original and record a structured ProcessingWarning on the ProductProfile.
"""
import io
from datetime import datetime, timezone

from PIL import Image

from app.schema import ProcessingWarning

ALPHA_MIN, ALPHA_MAX = 0.05, 0.95


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def alpha_coverage(png_bytes: bytes) -> float:
    """Fraction of pixels that are (mostly) opaque."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    opaque = sum(hist[128:])
    return opaque / (img.width * img.height)


def segment(photo_bytes: bytes) -> tuple[bytes | None, ProcessingWarning | None]:
    """Returns (cutout_png or None, ProcessingWarning or None).

    A warning is recoverable: the original image remains a reference asset."""
    try:
        from rembg import remove  # lazy: pulls onnxruntime + u2net weights
        cutout = remove(photo_bytes)
    except Exception as exc:
        return None, ProcessingWarning(
            code="segmentation_failed",
            message=f"rembg error: {exc}",
            created_at=_now(),
        )
    coverage = alpha_coverage(cutout)
    if coverage < ALPHA_MIN:
        return None, ProcessingWarning(
            code="segmentation_low_coverage",
            message=f"alpha coverage {coverage:.2f} below {ALPHA_MIN}",
            created_at=_now(),
        )
    if coverage > ALPHA_MAX:
        return None, ProcessingWarning(
            code="segmentation_high_coverage",
            message=f"alpha coverage {coverage:.2f} above {ALPHA_MAX}",
            created_at=_now(),
        )
    return cutout, None
