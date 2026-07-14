"""Prompt compiler — image target (gpt-image-2 via images.edit).

The compiled prompt carries ONLY the delta: camera / lighting / action /
environment (style vocabulary). Product identity comes exclusively from the
reference images passed through the API's image-conditioning input — the
prompt never describes the product's look.
"""
import hashlib

from pydantic import BaseModel

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
    prompt_hash: str


def compile_image_prompt(scene: Scene, style_id: str,
                         profile: ProductProfile) -> CompiledImagePrompt:
    vocab = STYLES[style_id]
    prompt = (
        "Photograph the exact product shown in the reference images — same shape, "
        "colors, materials, markings. Do not restyle or replace the product.\n"
        f"Action: {scene.action}\n"
        f"Camera: {scene.camera}\n"
        f"Lighting: {scene.lighting}\n"
        f"Environment: {vocab['environment']}\n"
        f"Palette: {vocab['palette']}\n"
        f"Texture: {vocab['texture']}\n"
        "Vertical 9:16 composition with headroom for captions. "
        "Photorealistic, no text, no logos, no watermarks, no people's faces in focus."
    )
    refs = [a.asset_id for a in profile.reference_images[:MAX_REFERENCE_IMAGES]]
    return CompiledImagePrompt(
        prompt=prompt,
        reference_asset_ids=refs,
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
    )
