"""Stage 4: strategy → list[Scene] intents. One structured-output call,
plus at most ONE corrective retry when machine validation fails.

Validators (machine-enforced, storyboard rejected on violation):
- scene durations sum to target_duration_s ±3s
- every vo_line is a contiguous slice of Strategy.script

Retry policy (Gate 2 decision): on the first validation failure the model
gets its original response back with the sanitized validation errors and one
chance to correct. A second failure raises STORYBOARD_VALIDATION_FAILED —
never more than one retry. Both calls are metered.
"""
import re
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.providers.openai_client import structured_call
from app.schema import CreativeBrief, Scene, Strategy
from app.stages.prompt_loader import load_prompt
from app.styles import STYLES


class SceneIntent(BaseModel):
    """One scene as the LLM plans it — ids/order are assigned by us."""
    duration_s: float = Field(ge=0.5, le=8.0)
    camera: str
    lighting: str
    action: str
    vo_line: Optional[str] = None
    caption: Optional[str] = None
    caption_style: Literal["bounce", "highlight", "plain"] = "bounce"
    transition_out: Literal["cut", "fade", "whip"] = "cut"


class StoryboardDraft(BaseModel):
    scenes: list[SceneIntent] = Field(min_length=3, max_length=12)


PROMPT_VERSION = "storyboard_v1"


class StoryboardValidationError(ValueError):
    """Raised after the single corrective retry also fails validation."""
    error_code = "STORYBOARD_VALIDATION_FAILED"
    cost_cents: int = 0
    meta: dict = {}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_durations(scenes: list[SceneIntent], target_duration_s: float) -> None:
    total = sum(s.duration_s for s in scenes)
    if abs(total - target_duration_s) > 3.0:
        raise StoryboardValidationError(
            f"scene durations sum to {total}s, target is {target_duration_s}s (±3s)"
        )


def validate_vo_lines(scenes: list[SceneIntent], script: str) -> None:
    """Every vo_line must be a contiguous slice of the script, in order."""
    norm_script = _norm(script)
    cursor = 0
    for i, scene in enumerate(scenes):
        if scene.vo_line is None:
            continue
        line = _norm(scene.vo_line)
        pos = norm_script.find(line, cursor)
        if pos == -1:
            raise StoryboardValidationError(
                f"scene {i}: vo_line is not a contiguous slice of the script "
                f"(or out of order): {scene.vo_line!r}"
            )
        cursor = pos + len(line)


def _validate(draft: StoryboardDraft, strategy: Strategy, brief: CreativeBrief) -> None:
    validate_durations(draft.scenes, brief.target_duration_s)
    validate_vo_lines(draft.scenes, strategy.script)


def run(strategy: Strategy, brief: CreativeBrief) -> tuple[list[Scene], int, dict]:
    """Returns (scenes, cost_cents, meta). meta records the prompt-template
    version and whether the corrective retry fired (observability)."""
    prompt = load_prompt(
        PROMPT_VERSION,
        scene_count=str(strategy.scene_count),
        target_duration_s=str(brief.target_duration_s),
        style_id=strategy.style_id,
        style_vocab="\n".join(
            f"- {k}: {v}" for k, v in STYLES[strategy.style_id].items()
        ),
        strategy=strategy.model_dump_json(exclude={"script"}, indent=2),
        script=strategy.script,
    )
    messages = [{"role": "user", "content": prompt}]
    draft, cost = structured_call(StoryboardDraft, messages)
    meta = {"prompt_version": PROMPT_VERSION, "llm_calls": 1,
            "corrective_retry": False}
    try:
        _validate(draft, strategy, brief)
    except StoryboardValidationError as first_error:
        # One corrective retry: original response + sanitized validation error.
        corrective = messages + [
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": (
                "Your storyboard failed machine validation:\n"
                f"- {first_error}\n"
                "Return a corrected storyboard that satisfies every rule in the "
                "original instructions. Keep everything else unchanged."
            )},
        ]
        draft, retry_cost = structured_call(StoryboardDraft, corrective)
        cost += retry_cost
        meta.update(llm_calls=2, corrective_retry=True,
                    first_error=str(first_error))
        try:
            _validate(draft, strategy, brief)
        except StoryboardValidationError as second_error:
            second_error.cost_cents = cost
            second_error.meta = meta
            raise  # STORYBOARD_VALIDATION_FAILED — never retry more than once
    scenes = [
        Scene(scene_id=f"scn_{uuid.uuid4().hex[:8]}", order=i, **intent.model_dump())
        for i, intent in enumerate(draft.scenes)
    ]
    return scenes, cost, meta
