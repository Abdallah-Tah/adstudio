"""Regression coverage for the 'image generation stuck forever' bug.

Root cause: autodiscover_tasks(["app.workers"]) registered NO tasks (looked for
a nonexistent app.workers.tasks module), so the worker consumed nothing. Plus:
no job timeout, no watchdog for orphaned jobs. These tests lock in the fixes.
"""
from datetime import datetime, timedelta, timezone

import respx

from app.schema import Generation
from app.workers import reaper


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _gen(status: str, *, created: float = 10, started: float | None = None) -> Generation:
    return Generation(
        generation_id="gen_x", scene_id="scn_x", kind="image",
        provider="openai", model="m", prompt="p", prompt_hash="h",
        status=status, created_at=_iso(created),
        started_at=_iso(started) if started is not None else None,
    )


def _video_gen(status: str, *, submitted: float = 10,
               heartbeat: float = 10) -> Generation:
    return Generation(
        generation_id="gen_v", scene_id="scn_v", kind="video",
        provider="fal", model="m", prompt="p", prompt_hash="h",
        status=status, created_at=_iso(submitted),
        provider_submitted_at=_iso(submitted),
        last_heartbeat_at=_iso(heartbeat),
    )


# ---- the actual bug: tasks must register --------------------------------

def test_task_modules_are_registered():
    """The worker's [tasks] list must not be empty — every enqueued job needs a
    registered consumer or it sits in Redis forever."""
    from app.workers.celery_app import celery_app
    celery_app.loader.import_default_modules()  # what the worker does on boot
    names = set(celery_app.tasks)
    assert "app.workers.images.generate_scene_image" in names
    assert "app.workers.videos.generate_scene_video" in names
    assert "app.workers.produce.produce_project" in names


def test_task_time_limits_configured():
    from app.workers.celery_app import celery_app
    assert celery_app.conf.task_soft_time_limit
    assert celery_app.conf.task_time_limit > celery_app.conf.task_soft_time_limit
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_routes["app.workers.images.generate_scene_image"] == {
        "queue": "images"
    }


# ---- watchdog / reaper --------------------------------------------------

def test_running_job_past_limit_is_overdue():
    assert reaper._overdue(_gen("running", started=200)) is not None   # > 180

def test_running_job_within_limit_is_fine():
    assert reaper._overdue(_gen("running", started=30)) is None

def test_queued_backlog_is_not_reaped():
    # normal single-worker backlog: queued a couple minutes, still fine
    assert reaper._overdue(_gen("queued", created=120)) is None

def test_queued_job_with_no_worker_times_out():
    assert reaper._overdue(_gen("queued", created=301)) is not None   # > 300

def test_terminal_jobs_are_never_reaped():
    for st in ("succeeded", "failed", "timed_out", "cancelled", "qc_rejected"):
        assert reaper._overdue(_gen(st, created=99999)) is None

def test_provider_video_with_fresh_heartbeat_is_not_reaped():
    assert reaper._overdue(
        _video_gen("provider_processing", submitted=400, heartbeat=30)
    ) is None

def test_provider_video_stale_heartbeat_times_out():
    assert reaper._overdue(
        _video_gen("provider_processing", submitted=400, heartbeat=240)
    ) is not None


# ---- provider timeout / failure surfacing -------------------------------

@respx.mock
def test_soft_time_limit_marks_failed(client, eager_worker, monkeypatch):
    """A hung provider call (SoftTimeLimitExceeded) must end as 'failed' with a
    readable note, never leave the job running."""
    from celery.exceptions import SoftTimeLimitExceeded

    from app.providers import openai_images
    from .conftest import create_test_project

    def boom(*a, **k):
        raise SoftTimeLimitExceeded()
    monkeypatch.setattr(openai_images, "generate_image", boom)

    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    gid = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]

    proj = client.get(f"/projects/{pid}").json()
    gen = next(g for s in proj["scenes"] for g in s["generations"]
               if g["generation_id"] == gid)
    assert gen["status"] == "failed"
    assert gen["error_code"] == "PROVIDER_TIMEOUT"
    assert "timeout" in (gen["qc_notes"] or "")


def test_failure_classification_separates_transient_and_permanent():
    from app.workers.images import classify_failure

    class RateLimit(Exception):
        status_code = 429

    class BadRequest(Exception):
        status_code = 400

    class ServerError(Exception):
        status_code = 503

    assert classify_failure(RateLimit("try later")).transient is True
    assert classify_failure(ServerError("unavailable")).transient is True
    bad = classify_failure(BadRequest("malformed reference image"))
    assert bad.transient is False
    assert bad.error_code == "INVALID_REFERENCE_IMAGE"


# ---- diagnostics endpoint ----------------------------------------------

def test_debug_jobs_endpoint(client):
    r = client.get("/debug/jobs")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"jobs", "counts", "worker", "queue_length", "watchdog"}
    assert body["watchdog"]["run_timeouts_s"]["image"] == reaper.RUN_TIMEOUTS["image"]
