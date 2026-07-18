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

from app import db
from app.schema import Generation, Project
from app.snapshots import snapshot_project

log = logging.getLogger("adstudio.reaper")

# Seconds a job may stay "running" before it's considered hung (worker died mid
# job). Generous vs. expected durations; video polls fal.ai for minutes.
RUN_TIMEOUTS = {"image": 180, "video": 600, "audio": 240}
# Seconds a job may stay "queued". Long enough to never false-positive on normal
# single-worker backlog, short enough that a missing worker is caught quickly.
QUEUE_TIMEOUT = 900


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
    if gen.status == "running":
        age = _age_seconds(gen.started_at or gen.created_at)
        limit = RUN_TIMEOUTS.get(gen.kind, 180)
    elif gen.status == "queued":
        age = _age_seconds(gen.created_at)
        limit = QUEUE_TIMEOUT
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
        gen.status = "failed"
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
