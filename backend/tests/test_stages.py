"""Recorded-fixture tests per stage — respx intercepts the OpenAI HTTP calls,
so nothing here touches a live API."""
import pytest
import respx
from httpx import Response

from app.schema import AssetRef, CreativeBrief, ProductProfile, Strategy
from app.stages import analysis, brief as brief_stage, storyboard, strategy as strategy_stage
from app.stages.brief import UserInputs

from .conftest import completion_payload, load_fixture

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
NOW = "2026-07-14T00:00:00Z"

REFS = [AssetRef(asset_id="ast_1", kind="reference",
                 uri="s3://test-bucket/ast_1.png", created_at=NOW)]


def mock_llm(*fixture_names: str) -> None:
    respx.post(OPENAI_URL).mock(side_effect=[
        Response(200, json=completion_payload(load_fixture(n))) for n in fixture_names
    ])


@respx.mock
def test_stage1_analysis():
    mock_llm("analysis")
    profile, cost = analysis.run(
        [(b"\x89PNG fake", "image/png")], "A ceramic pour-over set.", REFS
    )
    assert isinstance(profile, ProductProfile)
    assert profile.name == "Ceramic Pour-Over Set"
    assert profile.reference_images == REFS
    assert len(profile.key_benefits) <= 3
    assert cost > 0


@respx.mock
def test_stage2_brief_user_values_pass_through_untouched():
    mock_llm("brief")
    profile = ProductProfile(**load_fixture("analysis"), reference_images=REFS)
    user = UserInputs(offer="Buy one get one free", cta="Tap the link",
                      target_duration_s=25)
    brief, cost = brief_stage.run(profile, user)
    assert isinstance(brief, CreativeBrief)
    # user values win verbatim over the LLM fixture values
    assert brief.offer == "Buy one get one free"
    assert brief.cta == "Tap the link"
    assert brief.target_duration_s == 25
    # LLM fills what the user left blank
    assert brief.pain_points == ["bitter coffee", "messy counters"]
    assert cost > 0


@respx.mock
def test_stage3_strategy_style_passthrough_and_validation():
    mock_llm("strategy")
    profile = ProductProfile(**load_fixture("analysis"), reference_images=REFS)
    brief = CreativeBrief(**load_fixture("brief"))
    strategy, cost = strategy_stage.run(brief, profile, style_id="minimal_tech")
    assert isinstance(strategy, Strategy)
    assert strategy.style_id == "minimal_tech"  # user's choice wins
    assert cost > 0

    with pytest.raises(ValueError, match="unknown style"):
        strategy_stage.run(brief, profile, style_id="famous_brand_style")


@respx.mock
def test_stage4_storyboard():
    mock_llm("storyboard")
    brief = CreativeBrief(**load_fixture("brief"))
    strategy = Strategy(**load_fixture("strategy"))
    scenes, cost = storyboard.run(strategy, brief)
    assert len(scenes) == 5
    assert [s.order for s in scenes] == [0, 1, 2, 3, 4]
    assert all(s.scene_id.startswith("scn_") for s in scenes)
    total = sum(s.duration_s for s in scenes)
    assert abs(total - brief.target_duration_s) <= 3.0
    assert cost > 0
