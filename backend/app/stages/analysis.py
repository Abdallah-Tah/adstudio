"""Stage 1: photos + description → ProductProfile. One vision structured-output call."""
import base64

from pydantic import BaseModel, Field

from app.providers.openai_client import structured_call
from app.schema import AssetRef, ProductProfile
from app.stages.prompt_loader import load_prompt


class ProductFacts(BaseModel):
    """What the LLM extracts — reference_images are attached by us, not the model."""
    name: str
    brand: str
    category: str
    colors: list[str]
    materials: list[str]
    key_benefits: list[str] = Field(max_length=3)
    audience: str


def run(
    photos: list[tuple[bytes, str]],          # (raw bytes, mime type)
    description: str,
    reference_images: list[AssetRef],
) -> tuple[ProductProfile, int]:
    """Returns (profile, cost_cents)."""
    prompt = load_prompt("analysis_v1", description=description)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for raw, mime in photos:
        b64 = base64.b64encode(raw).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    facts, cost = structured_call(ProductFacts, [{"role": "user", "content": content}])
    profile = ProductProfile(
        **facts.model_dump(),
        reference_images=reference_images,
    )
    return profile, cost
