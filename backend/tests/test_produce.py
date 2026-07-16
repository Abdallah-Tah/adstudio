"""/produce chain: gating, readiness, finalize (VO+music+render+final QC),
status + report endpoints. Providers all mocked."""
import respx
from httpx import Response

from app.schema import Project
from app.stages.qc import QCVerdict
from app.workers import produce as produce_worker
from app.workers import videos as video_worker

from .conftest import create_test_project, load_fixture

FAL_URL = "https://queue.fal.run/fal-ai/kling-video/v3/standard/image-to-video"


def make_alignment(text: str, spc: float = 0.02) -> dict:
    return {
        "characters": list(text),
        "character_start_times_seconds": [i * spc for i in range(len(text))],
        "character_end_times_seconds": [(i + 1) * spc for i in range(len(text))],
    }


def _all_images_selected(client, eager_worker, good_provider) -> str:
    project = create_test_project(client)
    pid = project["project_id"]
    for s in project["scenes"]:
        gid = client.post(
            f"/projects/{pid}/scenes/{s['scene_id']}/generate-image"
        ).json()["generation_id"]
        client.post(f"/projects/{pid}/scenes/{s['scene_id']}/select-image",
                    json={"generation_id": gid})
    return pid


@respx.mock
def test_produce_refuses_without_approved_images(client, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    project = create_test_project(client)
    r = client.post(f"/projects/{project['project_id']}/produce")
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "SCENES_NOT_APPROVED"


@respx.mock
def test_produce_requires_all_provider_keys(client, eager_worker,
                                            good_provider, monkeypatch):
    pid = _all_images_selected(client, eager_worker, good_provider)
    monkeypatch.setenv("FAL_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = client.post(f"/projects/{pid}/produce")
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "PROVIDER_NOT_CONFIGURED"


@respx.mock
def test_produce_full_chain(client, sqlite_session, fake_storage, eager_worker,
                            good_provider, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid = _all_images_selected(client, eager_worker, good_provider)

    # stage 6: mock fal + QC pass; step every queued generation to completion
    monkeypatch.setattr(video_worker.qc, "run_qc",
                        lambda *a, **k: (QCVerdict(identity_ok=True,
                                                   artifacts=False,
                                                   notes="ok"), 1))
    submitted = {"n": 0}

    def fake_submit(model, payload):
        submitted["n"] += 1
        rid = f"req_{submitted['n']}"
        return {"request_id": rid,
                "status_url": f"{FAL_URL}/requests/{rid}/status",
                "response_url": f"{FAL_URL}/requests/{rid}"}

    monkeypatch.setattr(video_worker.fal_client, "submit", fake_submit)
    monkeypatch.setattr(video_worker.fal_client, "status",
                        lambda url: "COMPLETED")
    monkeypatch.setattr(video_worker.fal_client, "result",
                        lambda url: {"video": {"url": "https://fal/clip.mp4"}})
    monkeypatch.setattr(video_worker.fal_client, "download",
                        lambda url: b"\x00clip")

    def eager_video_delay(project_id, generation_id, poll=None):
        state, p = video_worker.run_video_step(
            sqlite_session, fake_storage, project_id, generation_id, poll)
        while state == "polling":
            state, p = video_worker.run_video_step(
                sqlite_session, fake_storage, project_id, generation_id, p)
        return state

    monkeypatch.setattr(video_worker.generate_scene_video, "delay",
                        lambda **kw: eager_video_delay(**kw))
    produce_calls = []
    monkeypatch.setattr(produce_worker.produce_project, "delay",
                        lambda project_id: produce_calls.append(project_id))

    r = client.post(f"/projects/{pid}/produce")
    assert r.status_code == 200
    assert len(r.json()["video_generations_queued"]) == 5
    assert produce_calls == [pid]

    # all scenes got videos auto-selected -> ready
    row_project = Project.model_validate(
        client.get(f"/projects/{pid}").json() | {})
    assert produce_worker.readiness(row_project) == "ready"
    status = client.get(f"/projects/{pid}/status").json()
    assert status["stage"] == "videos" or all(
        s["status"] == "video_ready" for s in status["scenes"])

    # stages 8-9: mock providers, run finalize directly against the session
    script = load_fixture("strategy")["script"]
    monkeypatch.setattr(produce_worker.audio_stage, "synthesize",
                        lambda s: (b"vo-mp3", make_alignment(s), 5))
    monkeypatch.setattr(produce_worker.render, "render",
                        lambda *a, **k: b"final-mp4")
    monkeypatch.setattr(produce_worker.qc, "run_qc",
                        lambda *a, **k: (QCVerdict(identity_ok=True,
                                                   artifacts=False,
                                                   caption_legible=True,
                                                   notes="clean"), 3))
    project = produce_worker.finalize(sqlite_session, fake_storage, row_project)
    assert project.final_render is not None
    assert project.voiceover.kind == "audio"
    assert project.voiceover.prompt == script
    assert project.cost.voice == 5
    assert project.music is None            # empty library -> skipped, recorded

    doc = client.get(f"/projects/{pid}").json()
    assert doc["final_render"]["kind"] == "render"
    status = client.get(f"/projects/{pid}/status").json()
    assert status["stage"] == "done"
    assert status["final_render_asset_id"] == doc["final_render"]["asset_id"]

    # final render is downloadable
    aid = doc["final_render"]["asset_id"]
    resp = client.get(f"/projects/{pid}/assets/{aid}")
    assert resp.status_code == 200 and resp.content == b"final-mp4"

    # report: full unit economics
    rep = client.get(f"/projects/{pid}/report").json()
    assert rep["cost_cents"]["total"] == rep["cost_cents"]["analysis"] + \
        rep["cost_cents"]["strategy"] + rep["cost_cents"]["images"] + \
        rep["cost_cents"]["videos"] + rep["cost_cents"]["voice"] + \
        rep["cost_cents"]["qc"]
    assert rep["counts"]["scenes"] == 5
    assert rep["counts"]["video_generations"] == 5
    assert rep["counts"]["qc_rejections"] == 0
    assert rep["wall_clock_s"]["storyboard"] is not None
