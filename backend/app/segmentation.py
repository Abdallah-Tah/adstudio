"""rembg product cutouts on upload, with mask refinement + diagnostics.

Cutout PNGs are stored as additional `reference` assets alongside the
originals. rembg struggles with fine comb teeth, transparent plastic, and
reflective materials, so the raw matte is refined before use: disconnected
alpha speckles are dropped (structures attached to the product body survive)
and the removal boundary is feathered. If the cutout is poor (alpha coverage
< 5% or > 95%) we keep only the original and record a structured
ProcessingWarning on the ProductProfile; a heavily fragmented raw matte keeps
the refined cutout but records a `segmentation_fragmented` warning so the
review UI can flag it.
"""
import io
from collections import deque
from datetime import datetime, timezone

from PIL import Image, ImageChops, ImageFilter

from app.schema import ProcessingWarning

ALPHA_MIN, ALPHA_MAX = 0.05, 0.95

# Refinement runs on a downsampled grid: big enough that comb teeth stay
# connected to the body, small enough that labeling is cheap on the Pi.
GRID_MAX_SIDE = 160
GRID_ALPHA_THRESHOLD = 16          # >= counts as content; semi-transparency kept
MIN_KEEP_CELLS = 3                 # absolute floor for a kept component
MIN_KEEP_FRACTION = 0.004          # of total foreground area
FRAGMENT_WARN_COUNT = 6            # raw components before we flag the matte


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def alpha_coverage(png_bytes: bytes) -> float:
    """Fraction of pixels that are (mostly) opaque."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    opaque = sum(hist[128:])
    return opaque / (img.width * img.height)


def _components(binary: list[list[bool]]) -> list[list[tuple[int, int]]]:
    """4-connected components of a small boolean grid (pure Python BFS)."""
    height, width = len(binary), len(binary[0]) if binary else 0
    seen = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if not binary[y][x] or seen[y][x]:
                continue
            queue = deque([(y, x)])
            seen[y][x] = True
            cells: list[tuple[int, int]] = []
            while queue:
                cy, cx = queue.popleft()
                cells.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width \
                            and binary[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        queue.append((ny, nx))
            components.append(cells)
    return components


def refine_cutout(png_bytes: bytes) -> tuple[bytes, int]:
    """Drop disconnected alpha speckles from a raw matte; feather the boundary.

    Returns (refined_png, raw_component_count). Structures physically attached
    to the product body (comb teeth, attachments) share its component and are
    never dropped; only islands smaller than MIN_KEEP_FRACTION of the total
    foreground go. If nothing needs dropping the input bytes pass through
    unchanged so the stored asset stays byte-identical to rembg's output.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = img.getchannel("A")
    scale = max(img.width, img.height) / GRID_MAX_SIDE
    if scale > 1:
        grid_size = (max(1, round(img.width / scale)), max(1, round(img.height / scale)))
        small = alpha.resize(grid_size, Image.Resampling.BILINEAR)
    else:
        small = alpha
    px = small.load()
    binary = [[px[x, y] >= GRID_ALPHA_THRESHOLD for x in range(small.width)]
              for y in range(small.height)]
    components = _components(binary)
    if not components:
        return png_bytes, 0
    total = sum(len(c) for c in components)
    min_keep = max(MIN_KEEP_CELLS, int(total * MIN_KEEP_FRACTION))
    dropped = [c for c in components if len(c) < min_keep]
    if not dropped:
        return png_bytes, len(components)

    keep_mask = Image.new("L", small.size, 255)
    keep_px = keep_mask.load()
    for cells in dropped:
        for y, x in cells:
            keep_px[x, y] = 0
    keep_mask = keep_mask.resize(img.size, Image.Resampling.BILINEAR)
    keep_mask = keep_mask.filter(ImageFilter.GaussianBlur(max(1.0, scale / 2)))
    img.putalpha(ImageChops.darker(alpha, keep_mask))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), len(components)


def segment(photo_bytes: bytes) -> tuple[bytes | None, ProcessingWarning | None]:
    """Returns (cutout_png or None, ProcessingWarning or None).

    A warning is recoverable: the original image remains a reference asset.
    Unlike coverage failures, a `segmentation_fragmented` warning still returns
    the (refined) cutout — the warning marks it for closer human review."""
    try:
        from rembg import remove  # lazy: pulls onnxruntime + u2net weights
        cutout = remove(photo_bytes)
    except Exception as exc:
        return None, ProcessingWarning(
            code="segmentation_failed",
            message=f"rembg error: {exc}",
            created_at=_now(),
        )
    try:
        cutout, raw_components = refine_cutout(cutout)
    except Exception:
        raw_components = 0        # refinement is best-effort; raw matte stands
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
    if raw_components > FRAGMENT_WARN_COUNT:
        return cutout, ProcessingWarning(
            code="segmentation_fragmented",
            message=(f"raw matte had {raw_components} disconnected regions "
                     "(fine teeth/transparency/reflections); speckles removed — "
                     "review the cutout"),
            created_at=_now(),
        )
    return cutout, None
