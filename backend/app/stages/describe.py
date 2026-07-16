"""Auto-describe: 1-4 product photos -> a short editable description suggestion.

A lightweight vision structured-output call (same model as stage 1). The result
prefills the create form so the user rarely writes the description by hand — they
just edit the suggestion. Never free-text parsed (hard rule)."""
import base64

from pydantic import BaseModel, Field

from app.providers.openai_client import structured_call

MAX_PHOTOS = 4


class Description(BaseModel):
    description: str = Field(max_length=600)


PROMPT = (
    "Look at these product photos and write a concise, factual 1-2 sentence "
    "description of the product: what it is, its material/color/form, and who it "
    "is for. Describe only what is visible. No marketing hype, no invented "
    "brand names, no claims you cannot see."
)


def run(photos: list[tuple[bytes, str]]) -> tuple[str, int]:
    """Returns (description, cost_cents). Uses up to MAX_PHOTOS images."""
    content: list[dict] = [{"type": "text", "text": PROMPT}]
    for raw, mime in photos[:MAX_PHOTOS]:
        b64 = base64.b64encode(raw).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    result, cost = structured_call(Description, [{"role": "user", "content": content}])
    return result.description.strip(), cost
