"""Phase 3 stage 6: video compiler, fal queue flow (mocked), gating + caps."""
import pytest
import respx
from httpx import Response

from app.compiler import kling_fal
from app.schema import Scene
from app.stages.qc import QCVerdict, qc_cost_cents
from app.workers import videos as video_worker

from .conftest import create_test_project


@pytest.fixture
def video_env(monkeypatch):
    """Both keys the video stage preflight requires (fal + QC)."""
    monkeypatch.setenv("FAL_KEY", "fal-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")


@pytest.fixture
def qc_pass(monkeypatch):
    monkeypatch.setattr(video_worker.qc, "run_qc",
                        lambda *a, **k: (QCVerdict(
                            identity_ok=True, artifacts=False,
                            notes="matches references"), 2))


@pytest.fixture
def qc_fail(monkeypatch):
    monkeypatch.setattr(video_worker.qc, "run_qc",
                        lambda *a, **k: (QCVerdict(
                            identity_ok=False, artifacts=True,
                            notes="product morphs mid-clip"), 2))

FAL_URL = "https://queue.fal.run/fal-ai/kling-video/v3/standard/image-to-video"
SUBMIT_DOC = {
    "request_id": "req_test123",
    "status_url": f"{FAL_URL}/requests/req_test123/status",
    "response_url": f"{FAL_URL}/requests/req_test123",
}


def make_scene(**overrides) -> Scene:
    base = dict(scene_id="scn_v", order=0, duration_s=3.5,
                camera="slow orbit", lighting="soft rim light",
                action="steam rises from the cup", selected_image="gen_img1")
    return Scene(**{**base, **overrides})


def test_video_compiler_snapshot():
    compiled = kling_fal.compile_video_prompt(make_scene(), "minimal_tech")
    assert compiled.model == kling_fal.VIDEO_MODEL
    assert compiled.prompt == (
        "Animate this exact scene. The product must stay exactly as shown in "
        "the start frame — same shape, colors, materials, markings.\n"
        "Action: steam rises from the cup\n"
        "Camera: slow orbit\n"
        "Lighting: soft rim light\n"
        "Motion style: slow push-ins, controlled reveals, subtle parallax\n"
        "Vertical 9:16. Smooth, realistic motion. No text, no logos, no people's "
        "faces in focus."
    )
    assert compiled.billed_duration_s == 4       # ceil(3.5), min 3
    assert compiled.start_image_generation_id == "gen_img1"
    # no selected image -> refuse to compile (video needs an approved image)
    with pytest.raises(ValueError, match="no selected image"):
        kling_fal.compile_video_prompt(make_scene(selected_image=None), "minimal_tech")


def test_video_cost_billed_seconds():
    assert kling_fal.billed_duration(1.0) == 3   # Kling floor
    assert kling_fal.billed_duration(7.2) == 8
    # 4s * $0.084 = $0.336 -> 34¢ ceil; 3s -> 26¢
    assert kling_fal.video_cost_cents(3.5) == 34
    assert kling_fal.video_cost_cents(1.0) == 26


def _selected_scene(client, eager_worker, good_provider):
    """Project with scene[0] having a selected image."""
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    gid = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    client.post(f"/projects/{pid}/scenes/{sid}/select-image", json={"generation_id": gid})
    return pid, sid


@respx.mock
def test_video_requires_selected_image(client, video_env):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
    assert r.status_code == 422
    assert "no selected image" in r.json()["detail"]


@respx.mock
def test_video_preflight_missing_key_consumes_nothing(
        client, eager_worker, good_provider, monkeypatch):
    pid, sid = _selected_scene(client, eager_worker, good_provider)
    monkeypatch.delenv("FAL_KEY", raising=False)
    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "PROVIDER_NOT_CONFIGURED"
    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    assert all(g["kind"] == "image" for g in scene["generations"])
    assert doc["cost"]["videos"] == 0


@respx.mock
def test_video_full_flow_submit_poll_complete(
        client, sqlite_session, fake_storage, eager_worker, good_provider,
        video_env, qc_pass, monkeypatch):
    pid, sid = _selected_scene(client, eager_worker, good_provider)

    # API enqueues (celery delay stubbed to no-op — we step manually)
    monkeypatch.setattr(video_worker.generate_scene_video, "delay",
                        lambda **kw: None)
    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
    assert r.status_code == 200
    vgid = r.json()["generation_id"]

    respx.post(FAL_URL).mock(return_value=Response(200, json=SUBMIT_DOC))
    respx.get(SUBMIT_DOC["status_url"]).mock(side_effect=[
        Response(200, json={"status": "IN_PROGRESS"}),
        Response(200, json={"status": "COMPLETED"}),
    ])
    respx.get(SUBMIT_DOC["response_url"]).mock(return_value=Response(
        200, json={"video": {"url": "https://v3.fal.media/files/clip.mp4",
                             "content_type": "video/mp4"}}))
    respx.get("https://v3.fal.media/files/clip.mp4").mock(
        return_value=Response(200, content=b"\x00\x00fake-mp4"))

    # step 1: submit
    state, poll = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, None)
    assert state == "polling" and poll["request_id"] == "req_test123"
    # the submitted payload disabled native audio and used a data URI start frame
    submitted = respx.calls[-1].request.read()
    assert b'"generate_audio": false' in submitted or b'"generate_audio":false' in submitted
    assert b"data:image/png;base64," in submitted
    # step 2: still in progress
    state, poll = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, poll)
    assert state == "polling"
    # step 3: completed
    state, _ = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, poll)
    assert state == "succeeded"

    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    vgen = next(g for g in scene["generations"] if g["kind"] == "video")
    assert vgen["status"] == "succeeded"
    assert vgen["asset"]["kind"] == "video"
    assert vgen["cost_cents"] == 34            # 3.5s scene -> 4 billed s
    assert doc["cost"]["videos"] == 34
    assert doc["cost"]["qc"] == 2              # QC metered per clip
    assert vgen["qc_notes"] == "matches references"
    assert vgen["stale"] is False

    # select it; scene becomes video_ready
    r = client.post(f"/projects/{pid}/scenes/{sid}/select-video",
                    json={"generation_id": vgid})
    assert r.status_code == 200
    assert r.json()["scenes"][0]["selected_video"] == vgid

    # further step calls are idempotent no-ops (append-only ledger)
    state, _ = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, poll)
    assert state == "succeeded"
    assert client.get(f"/projects/{pid}").json()["cost"]["videos"] == 34


LTX_URL = "https://queue.fal.run/fal-ai/ltxv-13b-098-distilled/image-to-video"
LTX_SUBMIT = {
    "request_id": "req_ltx",
    "status_url": f"{LTX_URL}/requests/req_ltx/status",
    "response_url": f"{LTX_URL}/requests/req_ltx",
}


@respx.mock
def test_video_flow_routes_to_selected_ltx_engine(
        client, sqlite_session, fake_storage, eager_worker, good_provider,
        video_env, qc_pass, monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE", "ltx")
    pid, sid = _selected_scene(client, eager_worker, good_provider)
    monkeypatch.setattr(video_worker.generate_scene_video, "delay",
                        lambda **kw: None)
    vgid = client.post(f"/projects/{pid}/scenes/{sid}/generate-video").json()["generation_id"]

    respx.post(LTX_URL).mock(return_value=Response(200, json=LTX_SUBMIT))
    respx.get(LTX_SUBMIT["status_url"]).mock(
        return_value=Response(200, json={"status": "COMPLETED"}))
    respx.get(LTX_SUBMIT["response_url"]).mock(return_value=Response(
        200, json={"video": {"url": "https://v3.fal.media/ltx.mp4"}}))
    respx.get("https://v3.fal.media/ltx.mp4").mock(
        return_value=Response(200, content=b"\x00ltx"))

    state, poll = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, None)
    assert state == "polling"
    # request hit the LTX endpoint with LTX param names (not kling's)
    submitted = respx.calls[-1].request
    assert "ltxv-13b-098-distilled" in str(submitted.url)
    body = submitted.read()
    assert b'"num_frames"' in body and b'"expand_prompt"' in body
    assert b'"start_image_url"' not in body and b'"generate_audio"' not in body

    state, _ = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, poll)
    assert state == "succeeded"
    doc = client.get(f"/projects/{pid}").json()
    vgen = next(g for g in doc["scenes"][0]["generations"] if g["kind"] == "video")
    assert vgen["model"] == "fal-ai/ltxv-13b-098-distilled/image-to-video"
    assert vgen["cost_cents"] == 7             # LTX ~5x cheaper than kling's 34
    assert doc["cost"]["videos"] == 7
    assert vgen["stale"] is False              # staleness recomputed per-engine


def test_qc_cost_ceils_to_cents():
    # 8 frames + 2 refs ≈ 12k input tokens on Haiku 4.5 → $0.012 → 2¢ ceil
    assert qc_cost_cents("claude-haiku-4-5", 12_000, 200) == 2
    assert qc_cost_cents("claude-haiku-4-5", 100, 10) == 1  # never undercount


@respx.mock
def test_qc_rejection_marks_and_autoretries(
        client, sqlite_session, fake_storage, eager_worker, good_provider,
        video_env, qc_fail, monkeypatch):
    pid, sid = _selected_scene(client, eager_worker, good_provider)
    delayed: list[str] = []
    monkeypatch.setattr(
        video_worker.generate_scene_video, "delay",
        lambda **kw: delayed.append(kw["generation_id"]))

    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
    vgid = r.json()["generation_id"]

    respx.post(FAL_URL).mock(return_value=Response(200, json=SUBMIT_DOC))
    respx.get(SUBMIT_DOC["status_url"]).mock(
        return_value=Response(200, json={"status": "COMPLETED"}))
    respx.get(SUBMIT_DOC["response_url"]).mock(return_value=Response(
        200, json={"video": {"url": "https://v3.fal.media/files/clip.mp4"}}))
    respx.get("https://v3.fal.media/files/clip.mp4").mock(
        return_value=Response(200, content=b"\x00fake"))

    state, poll = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, None)
    assert state == "polling"
    state, _ = video_worker.run_video_step(sqlite_session, fake_storage, pid, vgid, poll)
    assert state == "qc_rejected"

    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    vgen = next(g for g in scene["generations"]
                if g["generation_id"] == vgid)
    assert vgen["status"] == "qc_rejected"
    assert "morphs" in vgen["qc_notes"]
    assert doc["cost"]["videos"] == 34   # provider billed even on rejection
    assert doc["cost"]["qc"] == 2

    # the worker auto-retries below the cap
    retry_id = video_worker.maybe_autoretry_qc(sqlite_session, pid, vgid)
    assert retry_id is not None
    assert delayed == [vgid, retry_id]   # initial enqueue + fresh attempt
    doc = client.get(f"/projects/{pid}").json()
    videos = [g for g in doc["scenes"][0]["generations"] if g["kind"] == "video"]
    assert len(videos) == 2              # append-only: rejected + retry queued


@respx.mock
def test_video_attempt_cap_absolute(client, sqlite_session, fake_storage,
                                    eager_worker, good_provider, video_env,
                                    monkeypatch):
    pid, sid = _selected_scene(client, eager_worker, good_provider)
    monkeypatch.setattr(video_worker.generate_scene_video, "delay",
                        lambda **kw: None)
    respx.post(FAL_URL).mock(return_value=Response(500, json={"detail": "boom"}))

    for _ in range(3):
        r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
        assert r.status_code == 200
        vgid = r.json()["generation_id"]
        state, _ = video_worker.run_video_step(
            sqlite_session, fake_storage, pid, vgid, None)
        assert state == "failed"

    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-video")
    assert r.status_code == 409                 # cap of 3 per kind per scene
    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    assert sum(1 for g in scene["generations"] if g["kind"] == "video") == 3
    assert doc["cost"]["videos"] == 0           # failures cannot spend
