"""rembg product cutouts on upload.

Cutout PNGs are stored as additional `reference` assets alongside the
originals. If the cutout is poor (alpha coverage < 5% or > 95%) we keep only
the original and flag `segmentation_failed` in the creation notes.
"""
import io

from PIL import Image

ALPHA_MIN, ALPHA_MAX = 0.05, 0.95


def alpha_coverage(png_bytes: bytes) -> float:
    """Fraction of pixels that are (mostly) opaque."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    opaque = sum(hist[128:])
    return opaque / (img.width * img.height)


def segment(photo_bytes: bytes) -> tuple[bytes | None, str | None]:
    """Returns (cutout_png or None, failure_note or None)."""
    try:
        from rembg import remove  # lazy: pulls onnxruntime + u2net weights
        cutout = remove(photo_bytes)
    except Exception as exc:
        return None, f"segmentation_failed: rembg error: {exc}"
    coverage = alpha_coverage(cutout)
    if not (ALPHA_MIN <= coverage <= ALPHA_MAX):
        return None, f"segmentation_failed: alpha coverage {coverage:.2f} outside [0.05, 0.95]"
    return cutout, None
