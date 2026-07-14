"""Phase 0 acceptance test: Project schema round-trips through JSON."""
import json

import pytest
from pydantic import ValidationError

from app.schema import (
    AssetRef,
    CostLedger,
    CreativeBrief,
    ProductProfile,
    Project,
    Scene,
    Strategy,
)

NOW = "2026-07-14T00:00:00Z"


def make_project(scene_durations: list[float], target_duration_s: float) -> Project:
    return Project(
        project_id="prj_test",
        created_at=NOW,
        product=ProductProfile(
            name="Ceramic Pour-Over Set",
            brand="Test Brand",
            category="kitchen",
            colors=["matte white"],
            materials=["ceramic"],
            key_benefits=["even extraction", "easy cleanup"],
            audience="home coffee enthusiasts",
            reference_images=[
                AssetRef(asset_id="ast_1", kind="reference", uri="s3://ad-studio/ast_1.png", created_at=NOW)
            ],
        ),
        brief=CreativeBrief(
            audience="home coffee enthusiasts",
            pain_points=["bitter coffee", "messy setup"],
            desired_emotion="calm confidence",
            tone="warm",
            brand_voice="plainspoken",
            offer="20% off launch week",
            cta="Shop now",
            target_duration_s=target_duration_s,
        ),
        strategy=Strategy(
            hook="Pour, don't guess.",
            angle="problem_solution",
            emotional_trigger="relief",
            script="Bitter mornings end here. One pour, even extraction, clean bench. Shop now.",
            style_id="minimal_tech",
            scene_count=len(scene_durations),
        ),
        scenes=[
            Scene(
                scene_id=f"scn_{i}",
                order=i,
                duration_s=d,
                camera="slow push-in",
                lighting="soft window light",
                action="water spirals over grounds",
            )
            for i, d in enumerate(scene_durations)
        ],
    )


def test_roundtrip_json():
    project = make_project([5.0, 5.0, 5.0], target_duration_s=15.0)
    dumped = project.model_dump_json()
    restored = Project.model_validate_json(dumped)
    assert restored == project
    # and via plain dict/json too (matches JSONB storage path)
    restored2 = Project.model_validate(json.loads(dumped))
    assert restored2 == project


def test_duration_validator_rejects_mismatch():
    with pytest.raises(ValidationError):
        make_project([5.0, 5.0, 5.0], target_duration_s=30.0)


def test_duration_validator_accepts_within_tolerance():
    project = make_project([5.0, 5.0, 6.0], target_duration_s=14.0)  # off by 2s <= 3s
    assert project.brief.target_duration_s == 14.0


def test_scene_status_and_cost_ledger_defaults():
    project = make_project([5.0, 5.0, 5.0], target_duration_s=15.0)
    assert all(s.status == "draft" for s in project.scenes)
    assert project.cost.total == 0
    assert CostLedger(images=120, qc=30).total == 150


def test_min_scene_count_enforced():
    with pytest.raises(ValidationError):
        make_project([15.0], target_duration_s=15.0)
