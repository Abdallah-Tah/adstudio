"""Stage 3: brief + profile → Strategy. One structured-output call.

If the user chose a style, it is passed through untouched.
"""
from typing import Optional

from app.providers.openai_client import structured_call
from app.schema import CreativeBrief, ProductProfile, Strategy
from app.stages.prompt_loader import load_prompt
from app.styles import STYLES


def run(
    brief: CreativeBrief,
    profile: ProductProfile,
    style_id: Optional[str] = None,
) -> tuple[Strategy, int]:
    """Returns (strategy, cost_cents)."""
    if style_id is not None and style_id not in STYLES:
        raise ValueError(f"unknown style {style_id!r}; choose from {sorted(STYLES)}")
    prompt = load_prompt(
        "strategy_v1",
        brief=brief.model_dump_json(indent=2),
        profile=profile.model_dump_json(exclude={"reference_images"}, indent=2),
        style_ids=", ".join(STYLES) if style_id is None else style_id,
    )
    strategy, cost = structured_call(Strategy, [{"role": "user", "content": prompt}])
    if style_id is not None:
        strategy = strategy.model_copy(update={"style_id": style_id})
    elif strategy.style_id not in STYLES:
        raise ValueError(f"LLM returned unknown style_id {strategy.style_id!r}")
    return strategy, cost
