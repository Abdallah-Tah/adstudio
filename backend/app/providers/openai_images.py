"""Thin gpt-image-2 client (images.edit with reference-image conditioning).

Cost: the Images API bills per output image by quality/size. Base rates below
are OpenAI's published calculator numbers at 1024x1024, scaled by pixel count
and rounded up. If the response reports usage-based cost in future SDKs,
switch to it.
"""
import base64
import io
import math

from app.pricing import IMAGE_BASE_PIXELS as BASE_PIXELS
from app.pricing import IMAGE_BASE_RATES as BASE_RATES
from app.providers.openai_client import client


def image_cost_cents(size: str, quality: str) -> int:
    w, h = (int(x) for x in size.split("x"))
    dollars = BASE_RATES[quality] * (w * h) / BASE_PIXELS
    return math.ceil(dollars * 100)


def generate_image(
    prompt: str,
    reference_images: list[bytes],
    size: str,
    quality: str,
    model: str,
) -> tuple[bytes, int]:
    """Returns (png bytes, cost_cents). One images.edit call."""
    files = [io.BytesIO(raw) for raw in reference_images]
    for i, f in enumerate(files):
        f.name = f"reference_{i}.png"
    result = client().images.edit(
        model=model,
        image=files,
        prompt=prompt,
        size=size,
        quality=quality,
    )
    b64 = result.data[0].b64_json
    return base64.b64decode(b64), image_cost_cents(size, quality)
