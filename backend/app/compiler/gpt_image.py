"""Prompt compiler - image target (gpt-image-2 via images.edit).

The compiled prompt treats uploaded product references as authoritative and
only describes visibly supported identity constraints plus the scene delta. It
does not use the product name, which avoids semantic replacement.
"""
import hashlib

from pydantic import BaseModel

from app import product_references
from app.schema import ProductProfile, Scene
from app.styles import STYLES

IMAGE_MODEL = "gpt-image-2-2026-04-21"
IMAGE_PROVIDER = "openai"
# 9:16 vertical, both edges multiples of 16 (API constraint).
IMAGE_SIZE = "1088x1920"
IMAGE_QUALITY = "medium"
MAX_REFERENCE_IMAGES = 4          # originals + cutouts, capped to bound cost


class CompiledImagePrompt(BaseModel):
    provider: str = IMAGE_PROVIDER
    model: str = IMAGE_MODEL
    prompt: str
    size: str = IMAGE_SIZE
    quality: str = IMAGE_QUALITY
    reference_asset_ids: list[str]
    reference_types: list[str]
    generation_mode: str = "reference_generation"
    prompt_hash: str


def _identity_lines(profile: ProductProfile) -> list[str]:
    identity = profile.identity_profile
    lock = profile.identity_lock
    lines: list[str] = []
    lock_visible = {
        "locked shape": lock.shape,
        "locked silhouette": lock.silhouette,
        "locked dimensions/proportions": lock.dimensions,
        "locked transparent parts": ", ".join(lock.transparent_parts),
        "locked button layout": ", ".join(lock.buttons),
        "locked display": ", ".join(lock.display),
        "locked logo position": lock.logo_position or "",
        "locked attachment geometry": ", ".join(lock.attachment_geometry),
        "locked accessories/details": ", ".join(lock.accessories),
    }
    for label, value in lock_visible.items():
        if value:
            lines.append(f"- Preserve {label}: {value}")
    if lock.colors:
        lines.append(f"- Preserve exact locked colors: {', '.join(lock.colors)}")
    if lock.materials:
        lines.append(f"- Preserve exact locked materials: {', '.join(lock.materials)}")
    if lock.forbidden_changes:
        lines.extend(f"- Do not change: {item}" for item in lock.forbidden_changes)
    visible = {
        "silhouette": identity.silhouette,
        "primary shape": identity.primary_shape,
        "proportions": identity.proportions,
        "transparent components": ", ".join(identity.transparent_components),
        "button count": str(identity.button_count) if identity.button_count is not None else "",
        "button locations": ", ".join(identity.button_locations),
        "ports": ", ".join(identity.ports),
        "display details": ", ".join(identity.display_details),
        "logo location": identity.logo_location or "",
        "label layout": identity.label_layout or "",
        "attachments": ", ".join(identity.attachments),
        "distinctive features": ", ".join(identity.distinctive_features),
    }
    for label, value in visible.items():
        if value:
            lines.append(f"- Preserve {label}: {value}")
    colors = identity.primary_colors or profile.colors
    materials = identity.materials or profile.materials
    if colors:
        lines.append(f"- Preserve exact visible colors: {', '.join(colors)}")
    if materials:
        lines.append(f"- Preserve exact visible materials: {', '.join(materials)}")
    if identity.forbidden_changes:
        lines.extend(f"- Do not change: {item}" for item in identity.forbidden_changes)
    return lines


def compile_image_prompt(scene: Scene, style_id: str,
                         profile: ProductProfile) -> CompiledImagePrompt:
    vocab = STYLES[style_id]
    selected_refs = product_references.select_product_references(
        scene, profile, MAX_REFERENCE_IMAGES)
    if selected_refs:
        refs = [r.asset_id for r in selected_refs]
        ref_types = [r.reference_type for r in selected_refs]
    else:
        refs = [a.asset_id for a in profile.reference_images[:MAX_REFERENCE_IMAGES]]
        ref_types = ["reference"] * len(refs)
    mode = product_references.generation_mode_for_scene(
        scene, profile.product_references, profile.product_lock_mode)
    identity = "\n".join(_identity_lines(profile) or [
        "- Preserve the exact silhouette, proportions, colors, materials, markings, labels, attachments, ports, displays, and distinctive physical details visible in the supplied references.",
    ])
    mode_instruction = {
        "composite_exact_product": (
            "COMPOSITE MODE:\n"
            "Treat the uploaded product cutout/reference as a fixed asset. Generate the environment, lighting, shadows, reflections, and staging around it. Do not repaint or regenerate the product body. Match contact shadows and color temperature while keeping the product pixels/geometry visually identical.\n\n"
        ),
        "hybrid": (
            "HYBRID MODE:\n"
            "Generate hands, wrist/arm, hair, background, lighting, and contact shadows around the supplied product. Keep the uploaded product identity fixed and blend interaction around it. Do not let fingers, hair, or motion alter product geometry, controls, chamber, logo, display, or attachments.\n\n"
        ),
        "reference_generation": (
            "REFERENCE GENERATION MODE:\n"
            "Use full image generation only because the scene requires it or no valid cutout is available. The supplied references remain authoritative; recreate only the camera/environment delta and do not substitute a generic product.\n\n"
        ),
    }[mode]
    prompt = (
        "REFERENCE PRIORITY:\n"
        "The supplied product reference images are authoritative. Preserve the exact product identity. Do not redesign, replace, simplify, or infer an alternate product.\n\n"
        f"PRODUCT LOCK MODE: {profile.product_lock_mode}\n"
        "STRICT means the product is a fixed commercial asset: never redesign, reinterpret, simplify, or substitute it.\n\n"
        f"{mode_instruction}"
        "IDENTITY LOCK:\n"
        f"{identity}\n"
        "- Preserve exact attachment geometry, button count and placement, transparent components, colors, materials, logo/label placement, ports, display, and accessories when visible.\n"
        "- No new parts, no removed parts, no alternate model, no fantasy product, no generic replacement product.\n\n"
        "SCENE DELTA:\n"
        f"- Action: {scene.action}\n"
        f"- Camera: {scene.camera}\n"
        f"- Lighting: {scene.lighting}\n"
        f"- Environment: {vocab['environment']}\n"
        f"- Palette: {vocab['palette']}\n"
        f"- Texture: {vocab['texture']}\n\n"
        "NEGATIVE CONSTRAINTS:\n"
        "- no altered logo or invented text\n"
        "- no extra buttons, missing chambers, changed combs, modified body shape, or substituted attachments\n"
        "- no changed product proportions, material swaps, or redesigned labels\n"
        "Vertical 9:16 composition with headroom for captions. "
        "Photorealistic, no watermarks, no people's faces in focus."
    )
    return CompiledImagePrompt(
        prompt=prompt,
        reference_asset_ids=refs,
        reference_types=ref_types,
        generation_mode=mode,
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
    )
