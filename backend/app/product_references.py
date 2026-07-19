"""Canonical product reference metadata and deterministic scene selection."""
import hashlib
import io
import re
from datetime import datetime, timezone

from PIL import Image, ImageFilter, ImageStat

from app import segmentation
from app.schema import (
    AssetRef,
    ProcessingWarning,
    ProductLockMode,
    ProductProfile,
    ProductReference,
    Scene,
)


DETAIL_TERMS = {
    "button": re.compile(r"\b(button|control|switch)\b", re.I),
    "chamber": re.compile(r"\b(chamber|transparent|clear|tank|container)\b", re.I),
    "logo": re.compile(r"\b(logo|brand|mark)\b", re.I),
    "display": re.compile(r"\b(display|screen|digital|led)\b", re.I),
    "attachment": re.compile(r"\b(comb|attachment|head|port|usb|label)\b", re.I),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def image_dimensions(raw: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(raw)) as img:
        return img.width, img.height


def content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sharpness_score(img: Image.Image) -> float:
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return min(1.0, (stat.stddev[0] if stat.stddev else 0.0) / 48.0)


def reference_quality(raw: bytes, alpha: float | None = None) -> float:
    with Image.open(io.BytesIO(raw)) as img:
        width, height = img.width, img.height
        short_edge = min(width, height)
        resolution = min(1.0, short_edge / 1024.0)
        sharpness = _sharpness_score(img)
    coverage = 0.75
    if alpha is not None:
        coverage = 1.0 - min(1.0, abs(alpha - 0.45) / 0.45)
    return round(max(0.0, min(1.0, 0.45 * resolution + 0.35 * sharpness + 0.20 * coverage)), 3)


def classify_original(index: int, filename: str) -> str:
    name = filename.lower()
    if "front" in name:
        return "front"
    if "side" in name:
        return "side"
    if "back" in name:
        return "back"
    if "label" in name or "logo" in name:
        return "label_closeup"
    if any(term in name for term in ("detail", "button", "chamber", "comb", "port")):
        return "detail_closeup"
    return "hero" if index == 0 else "angle"


def upload_warnings(asset_id: str, raw: bytes, *, seen_hashes: set[str]) -> list[ProcessingWarning]:
    warnings: list[ProcessingWarning] = []
    digest = content_hash(raw)
    try:
        width, height = image_dimensions(raw)
    except Exception:
        return [ProcessingWarning(
            code="unsupported_image",
            asset_id=asset_id,
            message="image could not be opened for reference analysis",
            created_at=_now(),
        )]
    if digest in seen_hashes:
        warnings.append(ProcessingWarning(
            code="reference_duplicate",
            asset_id=asset_id,
            message="duplicate upload kept as an original reference but deprioritized",
            created_at=_now(),
        ))
    if min(width, height) < 512:
        warnings.append(ProcessingWarning(
            code="reference_low_resolution",
            asset_id=asset_id,
            message=f"reference is low resolution ({width}x{height})",
            created_at=_now(),
        ))
    aspect = max(width / height, height / width)
    if aspect > 3.0:
        warnings.append(ProcessingWarning(
            code="reference_extreme_crop",
            asset_id=asset_id,
            message=f"reference has an extreme crop/aspect ratio ({width}x{height})",
            created_at=_now(),
        ))
    return warnings


def make_original_reference(asset_id: str, filename: str, raw: bytes, index: int) -> ProductReference:
    width, height = image_dimensions(raw)
    return ProductReference(
        asset_id=asset_id,
        reference_type=classify_original(index, filename),
        quality_score=reference_quality(raw),
        is_primary=index == 0,
        width=width,
        height=height,
    )


def make_cutout_reference(asset_id: str, raw: bytes, *, primary: bool = False) -> ProductReference:
    width, height = image_dimensions(raw)
    alpha = segmentation.alpha_coverage(raw)
    return ProductReference(
        asset_id=asset_id,
        reference_type="cutout",
        quality_score=reference_quality(raw, alpha),
        is_primary=primary,
        alpha_coverage=round(alpha, 4),
        width=width,
        height=height,
    )


def _is_valid_cutout(ref: ProductReference) -> bool:
    alpha = ref.alpha_coverage
    return (
        ref.reference_type == "cutout"
        and alpha is not None
        and segmentation.ALPHA_MIN <= alpha <= segmentation.ALPHA_MAX
        and ref.quality_score >= 0.25
    )


def _scene_text(scene: Scene) -> str:
    return " ".join([scene.action, scene.camera, scene.lighting])


def _scene_needs_detail(scene: Scene) -> bool:
    text = _scene_text(scene)
    return any(pattern.search(text) for pattern in DETAIL_TERMS.values())


def _closest_angle(scene: Scene, refs: list[ProductReference]) -> ProductReference | None:
    text = _scene_text(scene).lower()
    preferred: list[str] = []
    if "front" in text or "face" in text:
        preferred.append("front")
    if "side" in text or "profile" in text:
        preferred.append("side")
    if "back" in text or "rear" in text:
        preferred.append("back")
    preferred.append("angle")
    for kind in preferred:
        match = [r for r in refs if r.reference_type == kind]
        if match:
            return sorted(match, key=lambda r: r.quality_score, reverse=True)[0]
    return None


IMPOSSIBLE_RECREATION_TERMS = (
    "underwater", "explode", "exploding", "melting", "transform", "morph",
    "extreme perspective", "large rotation", "full rotation", "crush",
)


def generation_mode_for_scene(
    scene: Scene,
    refs: list[ProductReference],
    lock_mode: ProductLockMode = "STRICT",
) -> str:
    text = _scene_text(scene).lower()
    has_cutout = any(_is_valid_cutout(r) for r in refs)
    if lock_mode == "REFERENCE_ONLY" or any(term in text for term in IMPOSSIBLE_RECREATION_TERMS):
        return "reference_generation"
    if lock_mode == "STRICT" and has_cutout:
        interaction = any(term in text for term in (
            "hand", "hold", "holding", "hair", "use", "using", "demo",
            "brush", "comb through", "apply", "press", "grip",
        ))
        return "hybrid" if interaction else "composite_exact_product"
    product_heavy = any(term in text for term in ("hero", "packaging", "label", "macro", "beauty shot"))
    interaction = any(term in text for term in ("hand", "hold", "holding", "hair", "use", "demo"))
    if has_cutout and product_heavy and not interaction:
        return "composite_exact_product"
    if has_cutout and interaction:
        return "hybrid"
    return "reference_generation"


def select_product_references(
    scene: Scene,
    profile: ProductProfile,
    max_refs: int = 4,
) -> list[ProductReference]:
    refs = profile.product_references
    if not refs:
        return []
    selected: list[ProductReference] = []

    def add(ref: ProductReference | None) -> None:
        if ref is None:
            return
        if ref.asset_id not in {r.asset_id for r in selected} and len(selected) < max_refs:
            selected.append(ref)

    primary = next((r for r in refs if r.is_primary), None)
    if primary is None:
        primary = sorted(refs, key=lambda r: r.quality_score, reverse=True)[0]
    add(primary)

    cutouts = sorted((r for r in refs if _is_valid_cutout(r)),
                     key=lambda r: r.quality_score, reverse=True)
    add(cutouts[0] if cutouts else None)
    add(_closest_angle(scene, refs))

    if _scene_needs_detail(scene):
        details = sorted(
            (r for r in refs if r.reference_type in {"detail_closeup", "label_closeup"}),
            key=lambda r: r.quality_score,
            reverse=True,
        )
        add(details[0] if details else None)

    for ref in sorted(refs, key=lambda r: r.quality_score, reverse=True):
        if len(selected) >= max_refs:
            break
        if ref.reference_type == "cutout" and not _is_valid_cutout(ref):
            continue
        add(ref)
    return selected


def selected_asset_refs(
    scene: Scene,
    profile: ProductProfile,
    max_refs: int = 4,
) -> list[AssetRef]:
    ref_meta = select_product_references(scene, profile, max_refs)
    if ref_meta:
        by_id = {a.asset_id: a for a in profile.reference_images}
        return [by_id[r.asset_id] for r in ref_meta if r.asset_id in by_id]
    return profile.reference_images[:max_refs]
