"""Storyboard validators + style guard + cost math."""
import pytest

from app.providers.openai_client import cost_cents
from app.stages.storyboard import (
    SceneIntent,
    StoryboardValidationError,
    validate_durations,
    validate_vo_lines,
)
from app.styles import BANNED_BRANDS, STYLES, _guard_styles

SCRIPT = "Your morning coffee should not taste bitter. One pour, no mess."


def intent(duration_s: float = 4.0, vo_line: str | None = None) -> SceneIntent:
    return SceneIntent(duration_s=duration_s, camera="static", lighting="soft",
                       action="pour", vo_line=vo_line)


def test_durations_within_tolerance_pass():
    validate_durations([intent(4.0), intent(4.0), intent(4.5)], 15.0)  # off 2.5s


def test_durations_out_of_tolerance_fail():
    with pytest.raises(StoryboardValidationError, match="durations"):
        validate_durations([intent(4.0), intent(4.0)], 15.0)  # off 7s


def test_vo_lines_contiguous_slices_pass():
    scenes = [
        intent(vo_line="Your morning coffee should not taste bitter."),
        intent(vo_line=None),
        intent(vo_line="One pour, no mess."),
    ]
    validate_vo_lines(scenes, SCRIPT)


def test_vo_line_rephrased_fails():
    scenes = [intent(vo_line="Your coffee shouldn't be bitter.")]
    with pytest.raises(StoryboardValidationError, match="contiguous slice"):
        validate_vo_lines(scenes, SCRIPT)


def test_vo_lines_out_of_order_fail():
    scenes = [
        intent(vo_line="One pour, no mess."),
        intent(vo_line="Your morning coffee should not taste bitter."),
    ]
    with pytest.raises(StoryboardValidationError):
        validate_vo_lines(scenes, SCRIPT)


def test_vo_line_whitespace_normalized_passes():
    scenes = [intent(vo_line="Your morning coffee\nshould not taste bitter.")]
    validate_vo_lines(scenes, SCRIPT)


def test_styles_have_no_trademarks_and_all_five_exist():
    assert sorted(STYLES) == ["bold_energy", "minimal_tech", "studio_luxury",
                              "ugc_handheld", "warm_lifestyle"]
    _guard_styles()  # must not raise
    blob = " ".join(" ".join(v.values()) for v in STYLES.values()).lower()
    assert not any(brand in blob for brand in BANNED_BRANDS)


def test_cost_cents_rounds_up_conservatively():
    # gpt-5.4-mini: $0.75/M in, $4.50/M out
    assert cost_cents("gpt-5.4-mini", 1_000_000, 0) == 75
    assert cost_cents("gpt-5.4-mini", 0, 1_000_000) == 450
    assert cost_cents("gpt-5.4-mini", 1200, 250) == 1  # fractions round up, never 0
