"""Thin gpt-image-2 client (images.edit with reference-image conditioning).

Cost: the Images API bills per output image by quality/size. Base rates below
are OpenAI's published calculator numbers at 1024x1024, scaled by pixel count
and rounded up. If the response reports usage-based cost in future SDKs,
switch to it.
"""
import base64
import io
import math

from app.providers.openai_client import client

# $ per image at 1024x1024, per quality (developers.openai.com pricing, 2026-07).
BASE_RATES = {"low": 0.006, "medium": 0.053, "high": 0.211}
BASE_PIXELS = 1024 * 1024


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
