"""Video-engine registry + selection.

Each engine module exposes the same surface so the worker stays engine-agnostic:
    VIDEO_MODEL: str
    compile_video_prompt(scene, style_id) -> CompiledVideoPrompt   (.provider,
        .model, .prompt, .prompt_hash, .start_image_generation_id)
    video_cost_cents(duration_s) -> int
    build_payload(compiled, start_image_uri) -> dict               (fal params)

Default engine is Kling (the Gate 2 primary). Override with `VIDEO_ENGINE=ltx`.
A stored Generation records its model, so staleness/cost recompute can look the
engine back up by model (`by_model`) instead of assuming the current default.
"""
import os

from app.compiler import kling_fal, ltx_fal

ENGINES = {
    "kling": kling_fal,
    "ltx": ltx_fal,
}
DEFAULT_ENGINE = "kling"

_BY_MODEL = {m.VIDEO_MODEL: m for m in ENGINES.values()}


def active_name() -> str:
    name = os.environ.get("VIDEO_ENGINE", DEFAULT_ENGINE).lower()
    return name if name in ENGINES else DEFAULT_ENGINE


def active_engine():
    return ENGINES[active_name()]


def by_model(model: str):
    """Engine module that produced a given model string, or None if unknown."""
    return _BY_MODEL.get(model)
