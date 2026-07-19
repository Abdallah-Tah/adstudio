"""Product identity preservation: cross-scene consistency gate, segmentation
mask refinement/diagnostics, and video frame-drift QC."""
import io
import sys
import types

import respx
from PIL import Image

from app import production_readiness
from app.schema import (
    Project,
    SceneConsistencyReport,
    SceneConsistencyVerdict,
)
from app.stages import scene_consistency
from app.stages.qc import QCVerdict

from .conftest import create_test_project

# captured at import time, before the autouse conftest stub patches the module
real_run_check = scene_consistency.run_check

NOW = "2026-07-15T00:00:00+00:00"


def _all_images_selected(client) -> str:
    project = create_test_project(client)
    pid = project["project_id"]
    client.post(f"/projects/{pid}/storyboard/approve")
    for s in project["scenes"]:
        gid = client.post(
            f"/projects/{pid}/scenes/{s['scene_id']}/generate-image"
        ).json()["generation_id"]
        client.post(f"/projects/{pid}/scenes/{s['scene_id']}/select-image",
                    json={"generation_id": gid})
    return pid


def _project(sqlite_session, pid: str) -> Project:
    from app import db
    return Project.model_validate(sqlite_session.get(db.ProjectRow, pid).data)


# ---------------------------------------------------------------- fingerprint

@respx.mock
def test_fingerprint_tracks_selected_images(client, sqlite_session,
                                            eager_worker, good_provider):
    pid = _all_images_selected(client)
    project = _project(sqlite_session, pid)
    original = scene_consistency.fingerprint(project)
    assert scene_consistency.is_current(project) is False   # no report yet

    project.scene_consistency = SceneConsistencyReport(
        checked_at=NOW, fingerprint=original, consistent=True)
    assert scene_consistency.is_current(project) is True

    # reselecting any scene image invalidates the report
    project.scenes[0].selected_image = "gen_other"
    assert scene_consistency.fingerprint(project) != original
    assert scene_consistency.is_current(project) is False


# ------------------------------------------------------------- run_check core

@respx.mock
def test_run_check_maps_verdicts_to_scene_ids(client, sqlite_session,
                                              fake_storage, eager_worker,
                                              good_provider, monkeypatch):
    pid = _all_images_selected(client)
    project = _project(sqlite_session, pid)
    scene_ids = [s.scene_id for s in sorted(project.scenes, key=lambda s: s.order)]

    captured = {}

    def fake_structured_call(model_cls, messages):
        captured["content"] = messages[0]["content"]
        return model_cls.model_validate({
            "consistent": False,
            "scenes": [
                {"scene_number": i, "consistent": i != 2,
                 "drifted_features": [] if i != 2 else ["button count"],
                 "notes": ""}
                # scene 5 verdict deliberately missing
                for i in range(1, 5)
            ],
        }), 7

    monkeypatch.setattr(scene_consistency, "structured_call", fake_structured_call)
    report, cost = real_run_check(project, fake_storage)

    assert cost == 7 and report.cost_cents == 7
    assert report.fingerprint == scene_consistency.fingerprint(project)
    assert report.consistent is False
    assert [v.scene_id for v in report.verdicts] == scene_ids
    by_id = {v.scene_id: v for v in report.verdicts}
    assert by_id[scene_ids[1]].drifted_features == ["button count"]
    assert by_id[scene_ids[1]].consistent is False
    # a scene the model skipped is treated as inconsistent, never silently passed
    assert by_id[scene_ids[4]].consistent is False
    assert "no verdict" in by_id[scene_ids[4]].notes
    # one reference image + one image per scene went into the single call
    images = [b for b in captured["content"] if b.get("type") == "image_url"]
    assert len(images) == 1 + len(scene_ids)


# ------------------------------------------------------ readiness + API gate

@respx.mock
def test_readiness_blocks_on_current_inconsistent_report(
        client, sqlite_session, eager_worker, good_provider, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid = _all_images_selected(client)
    project = _project(sqlite_session, pid)
    bad_scene = project.scenes[2].scene_id
    project.scene_consistency = SceneConsistencyReport(
        checked_at=NOW,
        fingerprint=scene_consistency.fingerprint(project),
        consistent=False,
        verdicts=[SceneConsistencyVerdict(
            scene_id=s.scene_id,
            consistent=s.scene_id != bad_scene,
            drifted_features=[] if s.scene_id != bad_scene else ["comb attachment"],
        ) for s in project.scenes],
    )
    readiness = production_readiness.validate(project)
    assert readiness.ready is False
    blockers = [r for r in readiness.blocking_reasons
                if r.code == "PRODUCT_IDENTITY_INCONSISTENT"]
    assert [b.scene_id for b in blockers] == [bad_scene]
    assert "comb attachment" in blockers[0].message

    # stale report (image reselected since) no longer blocks by itself
    project.scenes[2].selected_image = "gen_other"
    stale = production_readiness.validate(project)
    assert not any(r.code == "PRODUCT_IDENTITY_INCONSISTENT"
                   for r in stale.blocking_reasons)


@respx.mock
def test_stale_consistency_without_openai_key_blocks(
        client, sqlite_session, eager_worker, good_provider, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid = _all_images_selected(client)
    project = _project(sqlite_session, pid)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    readiness = production_readiness.validate(project)
    assert any(r.code == "PROVIDER_NOT_CONFIGURED" and "OPENAI_API_KEY" in r.message
               for r in readiness.blocking_reasons)


@respx.mock
def test_produce_runs_consistency_gate_and_blocks_on_drift(
        client, sqlite_session, eager_worker, good_provider, monkeypatch):
    for k in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(k, "test")
    pid = _all_images_selected(client)

    calls = {"n": 0}

    def failing_run_check(project, storage):
        calls["n"] += 1
        return SceneConsistencyReport(
            checked_at=NOW,
            fingerprint=scene_consistency.fingerprint(project),
            consistent=False,
            verdicts=[SceneConsistencyVerdict(
                scene_id=s.scene_id, consistent=(s.order != 0),
                drifted_features=[] if s.order != 0 else ["transparent chamber"],
            ) for s in project.scenes],
            cost_cents=9,
        ), 9

    monkeypatch.setattr(scene_consistency, "run_check", failing_run_check)
    r = client.post(f"/projects/{pid}/produce")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error_code"] == "PROJECT_NOT_READY"
    assert any(b["code"] == "PRODUCT_IDENTITY_INCONSISTENT"
               and "transparent chamber" in b["message"]
               for b in detail["blocking_reasons"])
    assert calls["n"] == 1
    # no production job was created and no videos queued
    project = _project(sqlite_session, pid)
    assert project.production_job is None
    assert not any(g.kind == "video" for s in project.scenes for g in s.generations)
    # report persisted + spend metered to the qc ledger
    assert project.scene_consistency is not None
    assert project.scene_consistency.consistent is False
    assert project.cost.qc >= 9

    # the stored (current) report keeps blocking readiness without a new call
    readiness = client.get(f"/projects/{pid}/production-readiness").json()
    assert any(b["code"] == "PRODUCT_IDENTITY_INCONSISTENT"
               for b in readiness["blocking_reasons"])
    assert calls["n"] == 1


@respx.mock
def test_identity_consistency_endpoint_stores_report(
        client, sqlite_session, eager_worker, good_provider):
    pid = _all_images_selected(client)
    r = client.post(f"/projects/{pid}/identity-consistency")
    assert r.status_code == 200
    doc = r.json()
    assert doc["consistent"] is True
    project = _project(sqlite_session, pid)
    assert project.scene_consistency is not None
    assert scene_consistency.is_current(project) is True
    assert len(doc["verdicts"]) == len(project.scenes)


@respx.mock
def test_identity_consistency_endpoint_requires_selected_images(
        client, eager_worker, good_provider, monkeypatch):
    project = create_test_project(client)
    pid = project["project_id"]
    monkeypatch.setattr(scene_consistency, "run_check", real_run_check)
    r = client.post(f"/projects/{pid}/identity-consistency")
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "IDENTITY_CONSISTENCY_INPUT_MISSING"


# --------------------------------------------------- segmentation refinement

def _speckled_cutout() -> bytes:
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    px = img.load()
    for x in range(100, 300):            # product body
        for y in range(100, 300):
            px[x, y] = (200, 40, 40, 255)
    for x in range(300, 360):            # thin comb tooth attached to the body
        for y in range(198, 203):
            px[x, y] = (200, 40, 40, 255)
    speckles = [(30, 30), (370, 40), (40, 370), (368, 368),
                (20, 200), (380, 200), (200, 20), (200, 380)]
    for sx, sy in speckles:              # disconnected rembg junk
        for dx in range(4):
            for dy in range(4):
                px[sx + dx, sy + dy] = (90, 90, 90, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_refine_cutout_drops_speckles_keeps_fine_structures():
    from app import segmentation

    refined, raw_components = segmentation.refine_cutout(_speckled_cutout())
    assert raw_components == 9           # body+tooth is one component + 8 speckles
    out = Image.open(io.BytesIO(refined)).convert("RGBA")
    assert out.getpixel((200, 200))[3] == 255        # body intact
    assert out.getpixel((350, 200))[3] > 128         # tooth survives
    for sx, sy in [(31, 31), (371, 41), (201, 381)]:
        assert out.getpixel((sx, sy))[3] < 16        # speckles removed


def test_refine_cutout_passthrough_when_clean():
    buf = io.BytesIO()
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    for x in range(50, 150):
        for y in range(40, 160):
            img.putpixel((x, y), (10, 10, 10, 255))
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    from app import segmentation

    refined, raw_components = segmentation.refine_cutout(raw)
    assert refined == raw                # byte-identical: nothing to drop
    assert raw_components == 1


def test_segment_flags_fragmented_matte_but_keeps_cutout(monkeypatch):
    from app import segmentation

    monkeypatch.setitem(sys.modules, "rembg",
                        types.SimpleNamespace(remove=lambda raw: _speckled_cutout()))
    cutout, warning = segmentation.segment(b"raw-photo")
    assert cutout is not None
    assert warning is not None
    assert warning.code == "segmentation_fragmented"
    assert warning.recoverable is True


@respx.mock
def test_pipeline_records_fragmented_warning_with_usable_cutout(
        client, monkeypatch):
    from datetime import datetime, timezone

    from app import pipeline
    from app.schema import ProcessingWarning
    from .conftest import tiny_png

    warning = ProcessingWarning(
        code="segmentation_fragmented",
        message="raw matte had 9 disconnected regions",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(pipeline.segmentation, "segment",
                        lambda raw: (tiny_png(), warning))
    project = create_test_project(client)
    warnings = project["product"]["processing_warnings"]
    fragmented = [w for w in warnings if w["code"] == "segmentation_fragmented"]
    assert len(fragmented) == 1
    assert fragmented[0]["asset_id"]
    # the cutout was still stored as a reference asset alongside the original
    assert len(project["product"]["reference_images"]) == 2


# ------------------------------------------------------- video frame drift QC

def test_qc_verdict_fails_on_frame_drift():
    drifted = QCVerdict(identity_ok=True, artifacts=False, frame_drift=True,
                        notes="chamber grows over time")
    assert drifted.passed is False
    clean = QCVerdict(identity_ok=True, artifacts=False, notes="ok")
    assert clean.frame_drift is False and clean.passed is True


def test_video_qc_prompt_tracks_drift_and_anchor(monkeypatch):
    from app.stages import qc

    monkeypatch.setattr(qc, "extract_keyframes", lambda clip: [b"\xff\xd8jpg"])
    captured = {}

    class FakeMessages:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                parsed_output=QCVerdict(identity_ok=True, artifacts=False,
                                        frame_drift=False, notes="ok"),
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

    monkeypatch.setattr(
        qc, "_client",
        lambda: types.SimpleNamespace(messages=FakeMessages()))

    from .test_images import make_scene

    verdict, cost = qc.run_qc(b"clip", [b"start-frame", b"ref"], make_scene(),
                              start_frame_included=True)
    assert verdict.passed is True
    text = captured["messages"][0]["content"][0]["text"]
    assert "frame_drift" in text
    assert "keyframes IN TIME ORDER" in text
    assert "approved still this clip was generated from" in text
    assert captured["output_format"] is QCVerdict

    verdict, _ = qc.run_qc(b"clip", [b"ref"], make_scene())
    text = captured["messages"][0]["content"][0]["text"]
    assert "approved still" not in text
