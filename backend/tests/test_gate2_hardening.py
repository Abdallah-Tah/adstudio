"""Gate 2 conditional-hold decisions: explicit storyboard approval,
provider preflight (consumes nothing), structured preprocessing warnings."""
import respx

from app import pipeline
from app.db import ProjectVersionRow
from app.schema import ProcessingWarning
from app.workers import images as image_worker

from .conftest import create_test_project, tiny_png

NOW = "2026-07-15T00:00:00Z"


@respx.mock
def test_batch_generation_requires_explicit_approval(client, eager_worker,
                                                     good_provider):
    project = create_test_project(client)
    pid = project["project_id"]
    assert project["storyboard_approval"]["status"] == "draft"

    # batch is gated on approval
    r = client.post(f"/projects/{pid}/generate-images")
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "STORYBOARD_NOT_APPROVED"

    # single-scene preview is allowed while draft
    sid = project["scenes"][0]["scene_id"]
    assert client.post(
        f"/projects/{pid}/scenes/{sid}/generate-image").status_code == 200

    # approve -> batch runs for the scenes without a selected image
    r = client.post(f"/projects/{pid}/storyboard/approve")
    assert r.status_code == 200
    approval = r.json()["storyboard_approval"]
    assert approval["status"] == "approved"
    assert approval["approved_at"] and approval["approved_version_id"]

    r = client.post(f"/projects/{pid}/generate-images")
    assert r.status_code == 200
    assert len(r.json()["queued"]) == 5  # nothing selected yet

    # editing intent after approval reverts the storyboard to draft
    client.post(f"/projects/{pid}/scenes/{sid}", json={"camera": "orbit"})
    doc = client.get(f"/projects/{pid}").json()
    assert doc["storyboard_approval"]["status"] == "draft"
    r = client.post(f"/projects/{pid}/generate-images")
    assert r.status_code == 409


@respx.mock
def test_missing_credential_consumes_nothing(client, sqlite_session,
                                             monkeypatch):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    versions_before = sqlite_session.query(ProjectVersionRow).filter_by(
        project_id=pid).count()

    monkeypatch.delenv("OPENAI_API_KEY")
    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "PROVIDER_NOT_CONFIGURED"

    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    assert scene["generation_attempts"] == 0      # no attempt consumed
    assert scene["generations"] == []             # no queued record left behind
    assert doc["cost"]["images"] == 0             # no cost
    versions_after = sqlite_session.query(ProjectVersionRow).filter_by(
        project_id=pid).count()
    assert versions_after == versions_before      # no mutation happened


@respx.mock
def test_segmentation_warning_is_structured(client, monkeypatch):
    warning = ProcessingWarning(
        code="segmentation_low_coverage",
        message="alpha coverage 0.01 below 0.05",
        created_at=NOW,
    )
    monkeypatch.setattr(pipeline.segmentation, "segment",
                        lambda raw: (None, warning))
    project = create_test_project(client)
    warnings = project["product"]["processing_warnings"]
    segmentation_warnings = [
        w for w in warnings if w["code"] == "segmentation_low_coverage"
    ]
    assert len(segmentation_warnings) == 1
    assert segmentation_warnings[0]["recoverable"] is True
    assert segmentation_warnings[0]["asset_id"]  # linked to the uploaded original
    # original photo is still a usable reference (cutout skipped)
    assert len(project["product"]["reference_images"]) == 1


def test_mutable_defaults_are_isolated():
    """Two instances must never share list state (Field(default_factory))."""
    from app.schema import Scene

    a = Scene(scene_id="a", order=0, duration_s=1, camera="c", lighting="l",
              action="x")
    b = Scene(scene_id="b", order=1, duration_s=1, camera="c", lighting="l",
              action="x")
    a.generations.append("sentinel")  # type: ignore[arg-type]
    assert b.generations == []
