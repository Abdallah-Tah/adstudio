"""Watchdog: fail generations whose worker died, hung, or never ran.

A job can get orphaned in a non-terminal state ("queued" with no worker, or
"running" when the worker process was killed before it could write a terminal
status). Celery's own time limits only help while the worker is alive — a dead
worker leaves the job stuck and the UI polling forever. This lazy reaper runs on
read paths (GET project / status) and transitions overdue jobs to "failed" so
the frontend always converges to true state within one poll.
"""
import logging
from datetime import datetime, timezone

from app import db, generation_config
from app.schema import Generation, Project
from app.snapshots import snapshot_project

log = logging.getLogger("adstudio.reaper")

# Seconds a job may stay "running" before it's considered hung (worker died mid
# job). Generous vs. expected durations; video polls fal.ai for minutes.
RUN_TIMEOUTS = {"image": 180, "video": 600, "audio": 240}
VIDEO_PROVIDER_SLA_TIMEOUT = generation_config.VIDEO_PROVIDER_SLA_TIMEOUT_S
VIDEO_PROVIDER_STALE_TIMEOUT = generation_config.VIDEO_PROVIDER_STALE_TIMEOUT_S
# Seconds a job may stay "queued" before recovery marks it terminal. Image jobs
# are hosted API waits, not local inference, so 5 minutes queued means the queue
# or worker has stalled.
QUEUE_TIMEOUTS = {"image": 300, "video": 900, "audio": 300}
QUEUE_TIMEOUT = 300


def _age_seconds(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return 0.0


def _overdue(gen: Generation) -> float | None:
    """Return the job's age if it has exceeded its limit, else None."""
    if gen.kind == "video" and gen.status in ("provider_queued", "provider_processing"):
        total = _age_seconds(gen.provider_submitted_at or gen.started_at or gen.created_at)
        stale = _age_seconds(gen.last_heartbeat_at or gen.last_provider_check_at)
        if total > VIDEO_PROVIDER_SLA_TIMEOUT:
            return total
        if stale > VIDEO_PROVIDER_STALE_TIMEOUT:
            return stale
        return None
    if gen.kind == "video" and gen.status in ("submitting", "downloading", "uploading", "qc_running"):
        age = _age_seconds(gen.last_heartbeat_at or gen.started_at or gen.created_at)
        limit = RUN_TIMEOUTS.get(gen.kind, 600)
        return age if age > limit else None
    if gen.status == "running":
        age = _age_seconds(gen.started_at or gen.created_at)
        limit = RUN_TIMEOUTS.get(gen.kind, 180)
    elif gen.status in ("queued", "retrying"):
        age = _age_seconds(gen.queued_at or gen.created_at)
        limit = QUEUE_TIMEOUTS.get(gen.kind, QUEUE_TIMEOUT)
    else:
        return None
    return age if age > limit else None


def reap_stuck(session, project: Project) -> list[str]:
    """Mark overdue queued/running generations as failed. Returns changed ids."""
    gens: list[Generation] = [g for s in project.scenes for g in s.generations]
    if project.voiceover is not None:
        gens.append(project.voiceover)

    changed: list[str] = []
    for gen in gens:
        age = _overdue(gen)
        if age is None:
            continue
        prior = gen.status
        gen.status = "timed_out"
        if gen.kind == "video" and prior in ("provider_queued", "provider_processing"):
            gen.error_code = "PROVIDER_PROCESSING_TIMEOUT"
            gen.error_message = "provider processing timeout"
        elif gen.kind == "video" and prior in ("submitting", "downloading", "uploading", "qc_running"):
            gen.error_code = "PROVIDER_STALE_JOB"
            gen.error_message = "provider heartbeat became stale"
        else:
            gen.error_code = "QUEUE_TIMEOUT" if prior in ("queued", "retrying") else "PROVIDER_TIMEOUT"
            gen.error_message = ("queued too long" if prior in ("queued", "retrying")
                                 else "provider timeout")
        gen.finished_at = datetime.now(timezone.utc).isoformat()
        gen.total_latency_ms = int(age * 1000)
        gen.qc_notes = (f"timed out (watchdog: stuck in '{prior}' for "
                        f"{int(age)}s with no worker progress)")
        changed.append(gen.generation_id)
        log.warning("reaped gen=%s kind=%s was=%s age=%ss",
                    gen.generation_id, gen.kind, prior, int(age))
        row = session.get(db.GenerationRow, gen.generation_id)
        if row is not None:
            row.data = gen.model_dump(mode="json")

    if changed:
        prow = session.get(db.ProjectRow, project.project_id)
        prow.data = project.model_dump(mode="json")
        snapshot_project(session, project, actor="watchdog",
                         reason=f"watchdog: timed out {len(changed)} stuck generation(s)")
        session.commit()
    return changed
