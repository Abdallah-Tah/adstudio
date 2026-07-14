"""Stage 4: strategy → list[Scene] intents. One structured-output call.

Validators (machine-enforced, storyboard rejected on violation):
- scene durations sum to target_duration_s ±3s
- every vo_line is a contiguous slice of Strategy.script
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


class StoryboardValidationError(ValueError):
    pass


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


def run(strategy: Strategy, brief: CreativeBrief) -> tuple[list[Scene], int]:
    """Returns (scenes, cost_cents)."""
    prompt = load_prompt(
        "storyboard_v1",
        scene_count=str(strategy.scene_count),
        target_duration_s=str(brief.target_duration_s),
        style_id=strategy.style_id,
        style_vocab="\n".join(
            f"- {k}: {v}" for k, v in STYLES[strategy.style_id].items()
        ),
        strategy=strategy.model_dump_json(exclude={"script"}, indent=2),
        script=strategy.script,
    )
    draft, cost = structured_call(StoryboardDraft, [{"role": "user", "content": prompt}])
    validate_durations(draft.scenes, brief.target_duration_s)
    validate_vo_lines(draft.scenes, strategy.script)
    scenes = [
        Scene(scene_id=f"scn_{uuid.uuid4().hex[:8]}", order=i, **intent.model_dump())
        for i, intent in enumerate(draft.scenes)
    ]
    return scenes, cost
