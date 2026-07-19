"""Stage 2: profile + user inputs → CreativeBrief. One structured-output call.

User-supplied values are authoritative: they are passed through untouched,
overwriting whatever the LLM returned for those fields.
"""
from typing import Optional

from pydantic import BaseModel

from app.providers.openai_client import structured_call
from app.schema import CreativeBrief, ProductProfile, ReferenceAdDNA
from app.stages.prompt_loader import load_prompt


class UserInputs(BaseModel):
    """What the user typed at project creation. None = user left it blank."""
    audience: Optional[str] = None
    offer: Optional[str] = None
    cta: Optional[str] = None
    tone: Optional[str] = None
    target_duration_s: Optional[float] = None
    style: Optional[str] = None               # consumed by stage 3, carried here
    remake_goal: Optional[str] = None
    reference_ad_dna: Optional[ReferenceAdDNA] = None


def run(profile: ProductProfile, user: UserInputs) -> tuple[CreativeBrief, int]:
    """Returns (brief, cost_cents)."""
    prompt = load_prompt(
        "brief_v1",
        profile=profile.model_dump_json(exclude={"reference_images"}, indent=2),
        user_inputs=user.model_dump_json(exclude={"style"}, indent=2),
    )
    brief, cost = structured_call(
        CreativeBrief, [{"role": "user", "content": prompt}]
    )
    # Pass-through: where the user gave a value, it wins verbatim.
    overrides = user.model_dump(exclude_none=True, exclude={"style"})
    if overrides:
        brief = brief.model_copy(update=overrides)
    return brief, cost
