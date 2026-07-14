"""API integration: full pipeline through mocked LLM + fake storage + sqlite.
Also verifies snapshot-per-mutation."""
import respx

from app.db import ProjectVersionRow

from .conftest import create_test_project


def test_schema_endpoint(client):
    schema = client.get("/schema").json()
    assert schema["title"] == "Project"
    assert "scenes" in schema["properties"]


@respx.mock
def test_full_pipeline_snapshots_and_versions(client, sqlite_session):
    project = create_test_project(client)
    pid = project["project_id"]
    assert len(project["scenes"]) == 5
    assert project["cost"]["analysis"] > 0
    assert project["cost"]["strategy"] > 0
    # user values passed through untouched
    assert project["brief"]["offer"] == "20% off launch week"
    assert project["brief"]["cta"] == "Shop now"
    # originals + rembg cutouts stored as reference assets
    assert len(project["product"]["reference_images"]) == 2

    # one snapshot per stage
    versions = client.get(f"/projects/{pid}/versions").json()
    assert [v["reason"] for v in versions] == [
        "stage1:analysis", "stage2:brief", "stage3:strategy", "stage4:storyboard",
    ]
    assert client.get(f"/projects/{pid}").json() == project
    listing = client.get("/projects").json()
    assert listing[0]["project_id"] == pid


@respx.mock
def test_scene_patch_snapshots_every_mutation(client, sqlite_session):
    project = create_test_project(client)
    pid = project["project_id"]
    sid = project["scenes"][0]["scene_id"]
    before = sqlite_session.query(ProjectVersionRow).filter_by(project_id=pid).count()

    resp = client.post(f"/projects/{pid}/scenes/{sid}",
                       json={"camera": "locked-off macro on the spout"})
    assert resp.status_code == 200
    assert resp.json()["scenes"][0]["camera"] == "locked-off macro on the spout"

    after = sqlite_session.query(ProjectVersionRow).filter_by(project_id=pid).count()
    assert after == before + 1  # every mutation snapshots

    # a patch that breaks the duration validator is rejected and NOT saved
    resp = client.post(f"/projects/{pid}/scenes/{sid}", json={"duration_s": 8.0})
    assert resp.status_code == 422
    assert client.get(f"/projects/{pid}").json()["scenes"][0]["duration_s"] == 3.5
    final = sqlite_session.query(ProjectVersionRow).filter_by(project_id=pid).count()
    assert final == after  # rejected mutation -> no snapshot


@respx.mock
def test_patch_unknown_scene_404(client):
    project = create_test_project(client)
    resp = client.post(f"/projects/{project['project_id']}/scenes/scn_nope",
                       json={"camera": "x"})
    assert resp.status_code == 404
