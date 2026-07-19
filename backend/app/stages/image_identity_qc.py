"""Product identity QC for generated still images."""
import base64
import io

from PIL import Image

from app import generation_config
from app.providers.openai_client import structured_call
from app.schema import ProductIdentityQC, ProductProfile, Scene

MAX_REFERENCES = 4


def _media_type(raw: bytes) -> str:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = (img.format or "").lower()
    except Exception:
        return "image/png"
    if fmt in {"jpeg", "jpg"}:
        return "image/jpeg"
    if fmt == "webp":
        return "image/webp"
    return "image/png"


def _image_url_block(raw: bytes) -> dict:
    b64 = base64.b64encode(raw).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{_media_type(raw)};base64,{b64}"},
    }


def _identity_text(profile: ProductProfile) -> str:
    identity = profile.identity_profile
    parts: list[str] = []
    if identity.silhouette:
        parts.append(f"silhouette: {identity.silhouette}")
    if identity.primary_shape:
        parts.append(f"shape: {identity.primary_shape}")
    if identity.proportions:
        parts.append(f"proportions: {identity.proportions}")
    if identity.primary_colors:
        parts.append(f"colors: {', '.join(identity.primary_colors)}")
    elif profile.colors:
        parts.append(f"colors: {', '.join(profile.colors)}")
    if identity.materials:
        parts.append(f"materials: {', '.join(identity.materials)}")
    elif profile.materials:
        parts.append(f"materials: {', '.join(profile.materials)}")
    if identity.transparent_components:
        parts.append(f"transparent components: {', '.join(identity.transparent_components)}")
    if identity.button_count is not None:
        parts.append(f"button count: {identity.button_count}")
    if identity.button_locations:
        parts.append(f"button locations: {', '.join(identity.button_locations)}")
    if identity.ports:
        parts.append(f"ports: {', '.join(identity.ports)}")
    if identity.display_details:
        parts.append(f"display: {', '.join(identity.display_details)}")
    if identity.attachments:
        parts.append(f"attachments: {', '.join(identity.attachments)}")
    if identity.distinctive_features:
        parts.append(f"distinctive features: {', '.join(identity.distinctive_features)}")
    return "; ".join(parts) or "Only use product details visible in the references."


def should_reject(verdict: ProductIdentityQC) -> bool:
    return (
        verdict.identity_score < generation_config.IMAGE_IDENTITY_QC_THRESHOLD
        or verdict.severe_failure
        or bool(verdict.invented_parts)
        or bool(verdict.missing_parts)
    )


def run_qc(
    generated_png: bytes,
    reference_images: list[bytes],
    scene: Scene,
    profile: ProductProfile,
) -> tuple[ProductIdentityQC, int]:
    """Returns (identity verdict, cost_cents)."""
    refs = reference_images[:MAX_REFERENCES]
    content: list[dict] = [{
        "type": "text",
        "text": (
            "You are a strict product identity QC gate for a generated ad still.\n"
            "The first images are authoritative references of the uploaded product. "
            "The final image is the generated output.\n\n"
            f"Visible product identity profile: {_identity_text(profile)}\n"
            f"Scene intent: action={scene.action}; camera={scene.camera}; lighting={scene.lighting}.\n\n"
            "Score only product identity. Background, hand placement, lighting, and camera angle may change. "
            "Reject if the product was redesigned, replaced by a generic product, lost core parts, gained invented core parts, or changed proportions/materials."
        ),
    }]
    for ref in refs:
        content.append(_image_url_block(ref))
    content.append(_image_url_block(generated_png))
    return structured_call(ProductIdentityQC, [{"role": "user", "content": content}])

