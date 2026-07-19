"""Upload pre-check (customer photo quality gate) and auto-pilot mode."""
import io

import pytest
import respx
from PIL import Image

from app.schema import Project
from app.stages import upload_precheck
from app.workers import autopilot
from app.workers import images as image_worker
from app.workers import produce as produce_worker
from app.workers import videos as video_worker

from .conftest import create_test_project, tiny_png


def big_png(width: int = 1200, height: int = 1500) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 60, 40)).save(buf, format="PNG")
    return buf.getvalue()


def good_cutout(*_a, **_k):
    img = Image.new("RGBA", (1200, 1500), (0, 0, 0, 0))
    for x in range(300, 900):
        for y in range(300, 1200):
            img.putpixel((x, y), (180, 60, 40, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), None


# ------------------------------------------------------------- pre-check unit

def test_precheck_good_photo(monkeypatch):
    monkeypatch.setattr(upload_precheck.segmentation, "segment", good_cutout)
    result = upload_precheck.run([("front.png", big_png(), "image/png")], use_ai=False)
    assert result.ok_to_proceed is True
    photo = result.photos[0]
    assert photo.verdict == "good" and photo.cutout_ok is True
    assert photo.issues == []
    assert "great" in result.summary


def test_precheck_flags_small_and_duplicate(monkeypatch):
    monkeypatch.setattr(upload_precheck.segmentation, "segment", good_cutout)
    small = tiny_png()
    big = big_png()
    result = upload_precheck.run(
        [("a.png", big, "image/png"),
         ("b.png", big, "image/png"),          # duplicate of a
         ("c.png", small, "image/png")],       # 8x8 -> too small
        use_ai=False)
    a, b, c = result.photos
    assert a.verdict == "good"
    assert b.verdict == "usable"
    assert any(i.code == "duplicate" for i in b.issues)
    assert c.verdict == "replace"
    assert any(i.code == "too_small" for i in c.issues)
    assert result.ok_to_proceed is True    # two photos still usable


def test_precheck_blocks_when_nothing_usable(monkeypatch):
    monkeypatch.setattr(upload_precheck.segmentation, "segment",
                        lambda raw: (None, None))
    result = upload_precheck.run([("t.png", tiny_png(), "image/png")], use_ai=False)
    assert result.ok_to_proceed is False
    assert "replace" in result.summary


def test_precheck_segmentation_verdicts(monkeypatch):
    from app.schema import ProcessingWarning

    def low_cov(raw):
        return None, ProcessingWarning(code="segmentation_low_coverage",
                                       message="", created_at="now")
    monkeypatch.setattr(upload_precheck.segmentation, "segment", low_cov)
    result = upload_precheck.run([("far.png", big_png(), "image/png")], use_ai=False)
    assert result.photos[0].verdict == "replace"
    assert any(i.code == "product_too_small" for i in result.photos[0].issues)


def test_precheck_ai_flags_marketing_composite(monkeypatch):
    monkeypatch.setattr(upload_precheck.segmentation, "segment", good_cutout)

    def fake_ai(photos):
        return upload_precheck._AIAssessment(findings=[
            upload_precheck._AIPhotoFinding(
                photo_number=1, clean_single_product=False,
                issues=["text_overlay", "multiple_products"],
                tip="Retake on a plain table without the banner."),
        ]), 3
    monkeypatch.setattr(upload_precheck, "ai_assess", fake_ai)
    result = upload_precheck.run([("listing.png", big_png(), "image/png")])
    assert result.ai_checked is True and result.cost_cents == 3
    photo = result.photos[0]
    assert photo.verdict == "replace"
    codes = {i.code for i in photo.issues}
    assert {"ai_text_overlay", "ai_multiple_products"} <= codes
    assert result.ok_to_proceed is False


# -------------------------------------------------------------- pre-check API

@respx.mock
def test_precheck_endpoint(client):
    r = client.post("/uploads/precheck",
                    files=[("photos", ("cup.png", big_png(), "image/png"))])
    assert r.status_code == 200
    doc = r.json()
    assert doc["ok_to_proceed"] is True
    assert doc["photos"][0]["filename"] == "cup.png"
    # client fixture stubs segmentation to a tiny opaque square -> cutout ok
    assert doc["photos"][0]["cutout_ok"] is True


# ------------------------------------------------------------------ auto mode

def _create_auto_project(client, monkeypatch) -> tuple[str, list[str]]:
    calls: list[str] = []
    monkeypatch.setattr(autopilot.run_autopilot, "delay",
                        lambda project_id: calls.append(project_id))
    import respx as respx_mod
    from httpx import Response

    from .conftest import completion_payload, load_fixture

    respx_mod.post("https://api.openai.com/v1/chat/completions").mock(side_effect=[
        Response(200, json=completion_payload(load_fixture(n)))
        for n in ("analysis", "brief", "strategy", "storyboard")
    ])
    resp = client.post(
        "/projects",
        files=[("photos", ("cup.png", tiny_png(), "image/png"))],
        data={"description": "A ceramic pour-over set.", "mode": "auto"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"], calls


@respx.mock
def test_auto_mode_create_approves_and_enqueues(client, monkeypatch):
    pid, calls = _create_auto_project(client, monkeypatch)
    assert calls == [pid]
    doc = client.get(f"/projects/{pid}").json()
    assert doc["storyboard_approval"]["status"] == "approved"
    assert doc["storyboard_approval"]["approved_by"] == "autopilot"
    assert doc["automation"]["mode"] == "auto"
    assert doc["automation"]["status"] == "generating_images"


@respx.mock
def test_autopilot_stop_returns_manual_control(client, monkeypatch):
    pid, _ = _create_auto_project(client, monkeypatch)
    r = client.post(f"/projects/{pid}/autopilot", json={"action": "stop"})
    assert r.status_code == 200
    doc = r.json()
    assert doc["automation"]["mode"] == "manual"
    assert doc["automation"]["status"] == "idle"


@respx.mock
def test_autopilot_ticks_to_producing(client, sqlite_session, fake_storage,
                                      eager_worker, good_provider, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid, _ = _create_auto_project(client, monkeypatch)

    video_queued: list[str] = []
    monkeypatch.setattr(video_worker.generate_scene_video, "delay",
                        lambda **kw: video_queued.append(kw["generation_id"]))
    produce_calls: list[str] = []
    monkeypatch.setattr(produce_worker.produce_project, "delay",
                        lambda project_id: produce_calls.append(project_id))

    from app import db

    # tick 1: queues an image per scene (eager worker completes them inline)
    state = autopilot.tick(sqlite_session, fake_storage, pid)
    assert state == "generating_images"
    project = Project.model_validate(sqlite_session.get(db.ProjectRow, pid).data)
    assert all(
        any(g.kind == "image" and g.status == "succeeded" for g in s.generations)
        for s in project.scenes
    )
    assert not any(s.selected_image for s in project.scenes)

    # tick 2: auto-selects the QC-passed images, runs the consistency gate,
    # creates the production job and queues every scene video
    state = autopilot.tick(sqlite_session, fake_storage, pid)
    assert state == "producing"
    project = Project.model_validate(sqlite_session.get(db.ProjectRow, pid).data)
    assert all(s.selected_image for s in project.scenes)
    assert project.scene_consistency is not None
    assert project.automation.status == "producing"
    assert project.production_job is not None
    assert len(video_queued) == len(project.scenes)
    assert produce_calls == [pid]

    # final render appears -> autopilot completes
    from app.schema import AssetRef
    row = sqlite_session.get(db.ProjectRow, pid)
    project = Project.model_validate(row.data)
    project.final_render = AssetRef(asset_id="ast_final", kind="render",
                                    uri="s3://test-bucket/final.mp4",
                                    created_at="2026-07-19T00:00:00Z")
    row.data = project.model_dump(mode="json")
    sqlite_session.commit()
    assert autopilot.tick(sqlite_session, fake_storage, pid) == "completed"


@respx.mock
def test_autopilot_needs_review_on_inconsistent_scenes(
        client, sqlite_session, fake_storage, eager_worker, good_provider,
        monkeypatch):
    from app.schema import SceneConsistencyReport, SceneConsistencyVerdict
    from app.stages import scene_consistency

    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid, _ = _create_auto_project(client, monkeypatch)

    def failing_run_check(project, storage):
        return SceneConsistencyReport(
            checked_at="2026-07-19T00:00:00+00:00",
            fingerprint=scene_consistency.fingerprint(project),
            consistent=False,
            verdicts=[SceneConsistencyVerdict(
                scene_id=s.scene_id, consistent=(s.order != 1),
                drifted_features=[] if s.order != 1 else ["comb attachment"],
            ) for s in project.scenes],
        ), 4
    monkeypatch.setattr(scene_consistency, "run_check", failing_run_check)

    autopilot.tick(sqlite_session, fake_storage, pid)          # generate images
    state = autopilot.tick(sqlite_session, fake_storage, pid)  # select + check
    assert state == "needs_review"
    from app import db
    project = Project.model_validate(sqlite_session.get(db.ProjectRow, pid).data)
    assert project.automation.status == "needs_review"
    assert "scene(s) 2" in project.automation.detail
    assert project.production_job is None


@respx.mock
def test_autopilot_needs_review_when_image_attempts_exhausted(
        client, sqlite_session, fake_storage, monkeypatch):
    pid, _ = _create_auto_project(client, monkeypatch)
    from app import db

    row = sqlite_session.get(db.ProjectRow, pid)
    project = Project.model_validate(row.data)
    project.scenes[0].generation_attempts = image_worker.MAX_ATTEMPTS
    row.data = project.model_dump(mode="json")
    sqlite_session.commit()
    monkeypatch.setattr(image_worker.generate_scene_image, "delay",
                        lambda *a, **k: None)

    state = autopilot.tick(sqlite_session, fake_storage, pid)
    assert state == "needs_review"
    project = Project.model_validate(sqlite_session.get(db.ProjectRow, pid).data)
    assert "image attempts" in project.automation.detail
