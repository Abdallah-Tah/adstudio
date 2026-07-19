"""Stage 6 worker: asynchronous, resumable video generation via fal queue."""
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis
from celery.signals import worker_ready

from app import db, generation_config as config
from app.compiler import engines
from app.providers import fal_client
from app.schema import AssetRef, Generation, Project, Scene
from app.stages import qc
from app.snapshots import snapshot_project
from app.storage import Storage
from app.workers.celery_app import REDIS, celery_app
from app.workers.images import AttemptCapReached, ProviderNotConfigured

MAX_ATTEMPTS = 3
ACTIVE_STATUSES = {
    "queued", "submitting", "provider_queued", "provider_processing",
    "downloading", "uploading", "qc_running", "retrying",
}
TERMINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled", "qc_rejected"}
INFRA_RETRY_DELAYS = config.VIDEO_INFRA_RETRY_DELAYS_S

log = logging.getLogger("adstudio.videos")


@dataclass(frozen=True)
class VideoGenerationStart:
    generation: Generation
    created: bool


class RetryableVideoError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms_between(a: str | None, b: str | None) -> int | None:
    start, end = _parse(a), _parse(b)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="worker", reason=reason)


def _sync_row(session, gen: Generation) -> None:
    row = session.get(db.GenerationRow, gen.generation_id)
    if row is not None:
        row.data = gen.model_dump(mode="json")


def _idempotency_key(
    project_id: str,
    scene_id: str,
    selected_image: str,
    provider: str,
    model: str,
    prompt_hash: str,
) -> str:
    raw = "|".join([project_id, scene_id, "video", selected_image,
                    prompt_hash, provider, model])
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_generation(session, project_id: str, generation_id: str):
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    scene = next(s for s in project.scenes
                 if any(g.generation_id == generation_id for g in s.generations))
    gen = next(g for g in scene.generations if g.generation_id == generation_id)
    return project, scene, gen


def _find_by_provider_job(session, provider_job_id: str):
    for row in session.query(db.GenerationRow).all():
        data = row.data or {}
        if data.get("provider_job_id") == provider_job_id:
            return row
    return None


def video_attempts(scene: Scene) -> int:
    """Creative video attempt count.

    Only jobs that reached the provider queue, produced provider cost, or got
    to a billable downstream stage consume the user's video attempt budget.
    Pre-submission auth/config/request failures stay in history but do not
    block another try after the environment/provider issue is fixed.
    """
    billable_statuses = {
        "provider_queued", "provider_processing", "downloading", "uploading",
        "qc_running", "succeeded", "qc_rejected", "cancelled", "timed_out",
    }
    return sum(
        1 for g in scene.generations
        if g.kind == "video"
        and (
            g.provider_job_id
            or g.cost_cents > 0
            or g.status in billable_statuses
        )
    )


def preflight_video(project: Project, scene: Scene) -> int:
    if not os.environ.get("FAL_KEY"):
        raise ProviderNotConfigured("FAL_KEY is not configured")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ProviderNotConfigured("ANTHROPIC_API_KEY is not configured (QC)")
    if scene.selected_image is None:
        raise ValueError(
            f"scene {scene.scene_id} has no selected image — video generation "
            "requires an approved image (hard rule)")
    start = next((g for g in scene.generations
                  if g.generation_id == scene.selected_image), None)
    if start is None or start.asset is None or start.status != "succeeded":
        raise ValueError("selected image has no downloadable asset")
    if video_attempts(scene) >= MAX_ATTEMPTS:
        raise AttemptCapReached(
            f"scene {scene.scene_id} already used {video_attempts(scene)}/"
            f"{MAX_ATTEMPTS} video attempts")
    return engines.active_engine().video_cost_cents(scene.duration_s)


def find_active_video_generation(project: Project, scene_id: str) -> Generation | None:
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    if scene.selected_image is None:
        return None
    engine = engines.active_engine()
    compiled = engine.compile_video_prompt(scene, project.strategy.style_id)
    key = _idempotency_key(
        project.project_id, scene.scene_id, scene.selected_image,
        compiled.provider, compiled.model, compiled.prompt_hash)
    return next((
        g for g in reversed(scene.generations)
        if g.kind == "video" and g.status in ACTIVE_STATUSES
        and g.idempotency_key == key
    ), None)


def start_video_generation(session, project: Project, scene_id: str) -> VideoGenerationStart:
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    active = find_active_video_generation(project, scene_id)
    if active is not None:
        return VideoGenerationStart(active, False)
    preflight_video(project, scene)
    engine = engines.active_engine()
    compiled = engine.compile_video_prompt(scene, project.strategy.style_id)
    start_gen = next(g for g in scene.generations
                     if g.generation_id == scene.selected_image)
    now = _now()
    generation = Generation(
        generation_id=f"gen_{uuid.uuid4().hex[:12]}",
        scene_id=scene_id,
        kind="video",
        provider=compiled.provider,
        model=compiled.model,
        prompt=compiled.prompt,
        prompt_hash=compiled.prompt_hash,
        reference_assets=[start_gen.asset.asset_id],
        source_generation_id=start_gen.generation_id,
        idempotency_key=_idempotency_key(
            project.project_id, scene_id, start_gen.generation_id,
            compiled.provider, compiled.model, compiled.prompt_hash),
        status="queued",
        created_at=now,
        queued_at=now,
    )
    scene.generations.append(generation)
    _save(session, project,
          f"generate-video queued {generation.generation_id} "
          f"(attempt {video_attempts(scene)}/{MAX_ATTEMPTS})")
    session.add(db.GenerationRow(
        generation_id=generation.generation_id,
        project_id=project.project_id,
        scene_id=scene_id,
        data=generation.model_dump(mode="json"),
    ))
    session.commit()
    return VideoGenerationStart(generation, True)


def _mark_failure(
    gen: Generation,
    exc: Exception,
    *,
    final_attempt: bool,
    timeout_code: str | None = None,
) -> bool:
    code, message, transient = fal_client.classify_error(exc)
    if timeout_code:
        code, message, transient = timeout_code, message, True
    gen.error_code = code
    gen.error_message = message
    gen.qc_notes = message
    if transient and not final_attempt and not gen.provider_job_id:
        gen.status = "retrying"
        gen.next_provider_check_at = _after(INFRA_RETRY_DELAYS[min(
            gen.attempt_number - 1, len(INFRA_RETRY_DELAYS) - 1)])
        return True
    gen.status = "failed"
    gen.finished_at = _now()
    gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
    return False


def _poll_delay(gen: Generation) -> int:
    checks = max(0, (gen.attempt_number or 1) - 1)
    return min(config.VIDEO_MAX_POLL_DELAY_S,
               config.VIDEO_INITIAL_POLL_DELAY_S + checks * 5)


def _provider_overdue(gen: Generation) -> str | None:
    now = datetime.now(timezone.utc)
    submitted = _parse(gen.provider_submitted_at)
    heartbeat = _parse(gen.last_heartbeat_at or gen.last_provider_check_at)
    if submitted and (now - submitted).total_seconds() > config.VIDEO_PROVIDER_SLA_TIMEOUT_S:
        return "PROVIDER_PROCESSING_TIMEOUT"
    if heartbeat and (now - heartbeat).total_seconds() > config.VIDEO_PROVIDER_STALE_TIMEOUT_S:
        return "PROVIDER_STALE_JOB"
    return None


def _normalize_status(status_doc) -> dict:
    if isinstance(status_doc, str):
        return {"status": status_doc}
    return dict(status_doc or {})


def _submit_provider_job(
    session,
    storage: Storage,
    project: Project,
    scene: Scene,
    gen: Generation,
    *,
    final_submission_attempt: bool,
) -> tuple[str, dict | None]:
    engine = engines.by_model(gen.model) or engines.active_engine()
    gen.status = "submitting"
    gen.started_at = gen.started_at or _now()
    gen.attempt_number = max(1, gen.attempt_number)
    gen.queue_wait_ms = _ms_between(gen.queued_at or gen.created_at, gen.started_at)
    gen.provider_called_at = _now()
    _sync_row(session, gen)
    _save(session, project, f"video generation submitting {gen.generation_id}")
    session.commit()

    try:
        compiled = engine.compile_video_prompt(scene, project.strategy.style_id)
        start_gen = next(g for g in scene.generations
                         if g.generation_id == scene.selected_image)
        image = storage.get_bytes(start_gen.asset.uri)
        payload = engine.build_payload(compiled, fal_client.data_uri(image))
        try:
            poll = fal_client.submit(
                gen.model,
                payload,
                webhook_url=config.FAL_WEBHOOK_URL,
                timeout=config.VIDEO_SUBMISSION_TIMEOUT_S,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            poll = fal_client.submit(gen.model, payload)
    except Exception as exc:
        retry = _mark_failure(
            gen, exc, final_attempt=final_submission_attempt,
            timeout_code=("PROVIDER_SUBMISSION_TIMEOUT"
                          if "timeout" in type(exc).__name__.lower() else None))
        _sync_row(session, gen)
        _save(session, project, f"video generation {gen.status} {gen.generation_id}")
        session.commit()
        if retry:
            raise RetryableVideoError(gen.error_code or "PROVIDER_UNAVAILABLE") from exc
        return gen.status, None

    gen.provider_job_id = poll["request_id"]
    gen.provider_status_url = poll.get("status_url")
    gen.provider_response_url = poll.get("response_url")
    gen.provider_cancel_url = poll.get("cancel_url")
    gen.provider_status = "IN_QUEUE"
    gen.provider_submitted_at = _now()
    gen.last_heartbeat_at = gen.provider_submitted_at
    gen.last_provider_check_at = gen.provider_submitted_at
    gen.next_provider_check_at = _after(config.VIDEO_INITIAL_POLL_DELAY_S)
    gen.status = "provider_queued"
    _sync_row(session, gen)
    _save(session, project, f"video generation provider queued {gen.generation_id} "
                            f"(fal {gen.provider_job_id})")
    session.commit()
    log.info("video_submit generation_id=%s project_id=%s scene_id=%s provider_job_id=%s",
             gen.generation_id, project.project_id, scene.scene_id, gen.provider_job_id)
    return "polling", poll


def _update_provider_status(gen: Generation, doc: dict) -> str:
    status = doc.get("status") or "UNKNOWN"
    gen.provider_status = status
    gen.last_provider_check_at = _now()
    gen.last_heartbeat_at = gen.last_provider_check_at
    if "queue_position" in doc and doc["queue_position"] is not None:
        try:
            gen.provider_progress = max(0.0, min(1.0, 1.0 / (float(doc["queue_position"]) + 1.0)))
        except (TypeError, ValueError):
            pass
    if status == "IN_QUEUE":
        gen.status = "provider_queued"
    elif status == "IN_PROGRESS":
        gen.status = "provider_processing"
        gen.provider_started_at = gen.provider_started_at or gen.last_provider_check_at
    return status


def _result_payload(gen: Generation) -> dict:
    if gen.provider_result:
        return gen.provider_result
    if not gen.provider_response_url:
        raise RuntimeError("provider response URL missing")
    return fal_client.result(gen.provider_response_url)


def _finalize_completed(
    session,
    storage: Storage,
    project: Project,
    scene: Scene,
    gen: Generation,
) -> str:
    if gen.status in TERMINAL_STATUSES:
        return gen.status
    engine = engines.by_model(gen.model) or engines.active_engine()
    if gen.status == "qc_running" and gen.asset:
        clip = storage.get_bytes(gen.asset.uri)
    else:
        payload = _result_payload(gen)
        gen.provider_result = gen.provider_result or payload
        video = payload.get("video") if isinstance(payload, dict) else None
        url = video.get("url") if isinstance(video, dict) else None
        if not url:
            gen.status = "failed"
            gen.error_code = "PROVIDER_INVALID_REQUEST"
            gen.error_message = "provider result did not include a video URL"
            gen.qc_notes = gen.error_message
            gen.finished_at = _now()
            _sync_row(session, gen)
            _save(session, project, f"video generation failed {gen.generation_id}")
            session.commit()
            return "failed"

        gen.status = "downloading"
        _sync_row(session, gen)
        _save(session, project, f"video generation downloading {gen.generation_id}")
        session.commit()
        t0 = time.monotonic()
        try:
            try:
                clip = fal_client.download(url, timeout=config.VIDEO_DOWNLOAD_TIMEOUT_S)
            except TypeError as exc:
                if "unexpected keyword" not in str(exc):
                    raise
                clip = fal_client.download(url)
        except Exception as exc:
            code, msg, transient = fal_client.classify_error(exc)
            gen.status = "failed"
            gen.error_code = "PROVIDER_DOWNLOAD_FAILED" if transient else code
            gen.error_message = "provider video download failed"
            gen.qc_notes = gen.error_message
            gen.finished_at = _now()
            _sync_row(session, gen)
            _save(session, project, f"video generation failed {gen.generation_id}")
            session.commit()
            return "failed"
        gen.download_latency_ms = int((time.monotonic() - t0) * 1000)

        gen.status = "uploading"
        _sync_row(session, gen)
        _save(session, project, f"video generation uploading {gen.generation_id}")
        session.commit()
        t0 = time.monotonic()
        try:
            asset_id = f"ast_{uuid.uuid4().hex[:12]}"
            uri = storage.put_bytes(clip, f"{project.project_id}/videos/{asset_id}.mp4",
                                    "video/mp4")
        except Exception:
            gen.status = "failed"
            gen.error_code = "ASSET_UPLOAD_FAILED"
            gen.error_message = "asset upload failed"
            gen.qc_notes = gen.error_message
            gen.finished_at = _now()
            _sync_row(session, gen)
            _save(session, project, f"video generation failed {gen.generation_id}")
            session.commit()
            return "failed"
        gen.upload_latency_ms = int((time.monotonic() - t0) * 1000)
        gen.asset_uploaded_at = _now()
        gen.asset = AssetRef(
            asset_id=asset_id, kind="video", uri=uri,
            generated_from=scene.scene_id,
            reference_assets=gen.reference_assets,
            created_at=gen.asset_uploaded_at,
        )
        gen.cost_cents = gen.cost_cents or engine.video_cost_cents(scene.duration_s)
        project.cost.videos += gen.cost_cents

    gen.status = "qc_running"
    _sync_row(session, gen)
    _save(session, project, f"video generation qc running {gen.generation_id}")
    session.commit()
    t0 = time.monotonic()
    refs: list[bytes] = []
    source = next((
        candidate for candidate in scene.generations
        if candidate.generation_id == gen.source_generation_id and candidate.asset
    ), None)
    if source and source.asset:
        refs.append(storage.get_bytes(source.asset.uri))
    for ref in project.product.reference_images:
        if len(refs) >= qc.MAX_REFERENCES:
            break
        refs.append(storage.get_bytes(ref.uri))
    try:
        verdict, qc_cost = qc.run_qc(
            clip, refs, scene,
            start_frame_included=bool(source and source.asset))
    except Exception as exc:
        gen.qc_latency_ms = int((time.monotonic() - t0) * 1000)
        gen.status = "failed"
        gen.error_code = "QC_FAILED"
        gen.error_message = "video QC failed"
        gen.qc_notes = str(exc).splitlines()[0][:240] or gen.error_message
        gen.finished_at = _now()
        gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
        _sync_row(session, gen)
        _save(session, project, f"video generation failed {gen.generation_id}")
        session.commit()
        return "failed"
    gen.qc_latency_ms = int((time.monotonic() - t0) * 1000)
    project.cost.qc += qc_cost
    gen.qc_notes = verdict.notes
    gen.provider_completed_at = gen.provider_completed_at or _now()
    gen.provider_latency_ms = _ms_between(gen.provider_submitted_at, gen.provider_completed_at)
    gen.finished_at = _now()
    gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
    gen.status = "succeeded" if verdict.passed else "qc_rejected"
    if gen.status == "succeeded" and scene.selected_video is None:
        scene.selected_video = gen.generation_id
    elif gen.status == "qc_rejected":
        gen.error_code = "QC_REJECTED"
        gen.error_message = verdict.notes
    _sync_row(session, gen)
    _save(session, project, f"video generation {gen.status} {gen.generation_id}")
    session.commit()
    return gen.status


def run_video_step(
    session,
    storage: Storage,
    project_id: str,
    generation_id: str,
    poll: dict | None = None,
    *,
    final_submission_attempt: bool = True,
) -> tuple[str, dict | None]:
    project, scene, gen = _load_generation(session, project_id, generation_id)
    if gen.status in TERMINAL_STATUSES:
        return gen.status, None
    if gen.status in ("queued", "retrying", "submitting") and not gen.provider_job_id:
        return _submit_provider_job(
            session, storage, project, scene, gen,
            final_submission_attempt=final_submission_attempt)

    overdue = _provider_overdue(gen)
    if overdue:
        gen.status = "timed_out"
        gen.error_code = overdue
        gen.error_message = ("provider processing timeout" if overdue == "PROVIDER_PROCESSING_TIMEOUT"
                             else "provider heartbeat became stale")
        gen.qc_notes = gen.error_message
        gen.finished_at = _now()
        gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
        _sync_row(session, gen)
        _save(session, project, f"video generation timed out {generation_id}")
        session.commit()
        return "timed_out", None

    if gen.provider_result:
        return _finalize_completed(session, storage, project, scene, gen), None

    status_url = gen.provider_status_url or (poll or {}).get("status_url")
    if not status_url:
        gen.status = "failed"
        gen.error_code = "UNKNOWN_PROVIDER_ERROR"
        gen.error_message = "provider status URL missing"
        gen.qc_notes = gen.error_message
        gen.finished_at = _now()
        _sync_row(session, gen)
        _save(session, project, f"video generation failed {generation_id}")
        session.commit()
        return "failed", None

    try:
        doc = _normalize_status(fal_client.status(status_url))
    except Exception as exc:
        retry = _mark_failure(gen, exc, final_attempt=final_submission_attempt)
        _sync_row(session, gen)
        _save(session, project, f"video generation {gen.status} {generation_id}")
        session.commit()
        if retry:
            raise RetryableVideoError(gen.error_code or "PROVIDER_UNAVAILABLE") from exc
        return gen.status, None

    status = _update_provider_status(gen, doc)
    if status in ("IN_QUEUE", "IN_PROGRESS"):
        gen.next_provider_check_at = _after(_poll_delay(gen))
        gen.attempt_number += 1
        _sync_row(session, gen)
        _save(session, project, f"video generation {gen.status} {generation_id}")
        session.commit()
        return "polling", {
            "request_id": gen.provider_job_id,
            "status_url": status_url,
            "response_url": gen.provider_response_url,
            "cancel_url": gen.provider_cancel_url,
        }
    if status == "COMPLETED":
        gen.provider_completed_at = _now()
        gen.provider_result = gen.provider_result or None
        _sync_row(session, gen)
        _save(session, project, f"video generation provider complete {generation_id}")
        session.commit()
        return _finalize_completed(session, storage, project, scene, gen), None

    gen.status = "failed"
    gen.error_code = doc.get("error_type") or "UNKNOWN_PROVIDER_ERROR"
    gen.error_message = str(doc.get("error") or "provider failed")[:240]
    gen.qc_notes = gen.error_message
    gen.finished_at = _now()
    _sync_row(session, gen)
    _save(session, project, f"video generation failed {generation_id}")
    session.commit()
    return "failed", None


def maybe_autoretry_qc(session, project_id: str, generation_id: str) -> str | None:
    # Paid video quality retries require user approval by default.
    return None


def handle_fal_webhook(session, payload: dict) -> dict:
    request_id = payload.get("request_id")
    if not request_id:
        return {"ok": False, "reason": "missing request_id"}
    row = _find_by_provider_job(session, request_id)
    if row is None:
        return {"ok": False, "reason": "unknown request_id"}
    project, _, gen = _load_generation(session, row.project_id, row.generation_id)
    if gen.status in TERMINAL_STATUSES:
        return {"ok": True, "generation_id": gen.generation_id, "status": gen.status}
    if payload.get("status") == "OK" and gen.provider_result:
        return {"ok": True, "generation_id": gen.generation_id, "status": gen.status}
    gen.provider_status = payload.get("status")
    gen.last_provider_check_at = _now()
    gen.last_heartbeat_at = gen.last_provider_check_at
    should_finalize = False
    if payload.get("status") == "OK":
        gen.provider_result = payload.get("payload") or {}
        gen.provider_completed_at = _now()
        gen.status = "provider_processing"
        should_finalize = True
    elif payload.get("status") == "ERROR":
        gen.status = "failed"
        gen.error_code = "UNKNOWN_PROVIDER_ERROR"
        gen.error_message = str(payload.get("error") or "provider failed")[:240]
        gen.qc_notes = gen.error_message
        gen.finished_at = _now()
    _sync_row(session, gen)
    _save(session, project, f"fal webhook {gen.generation_id} {gen.provider_status}")
    session.commit()
    if should_finalize:
        generate_scene_video.delay(project_id=project.project_id,
                                   generation_id=gen.generation_id)
    return {"ok": True, "generation_id": gen.generation_id, "status": gen.status}


def _lock(generation_id: str, ttl: int = 120):
    try:
        rc = redis.Redis.from_url(REDIS)
        key = f"lock:video:{generation_id}"
        if not rc.set(key, "1", nx=True, ex=ttl):
            return None
        return rc, key
    except Exception:
        return False


@celery_app.task(bind=True, name="app.workers.videos.generate_scene_video",
                 max_retries=len(INFRA_RETRY_DELAYS))
def generate_scene_video(self, project_id: str, generation_id: str,
                         poll: dict | None = None) -> str:
    lock = _lock(generation_id)
    if lock is None:
        return "locked"
    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    try:
        final_attempt = self.request.retries >= len(INFRA_RETRY_DELAYS)
        state, next_poll = run_video_step(
            session, Storage(), project_id, generation_id, poll,
            final_submission_attempt=final_attempt)
    except RetryableVideoError as exc:
        countdown = INFRA_RETRY_DELAYS[self.request.retries]
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        session.close()
        if isinstance(lock, tuple):
            lock[0].delete(lock[1])
    if state == "polling":
        delay = config.VIDEO_INITIAL_POLL_DELAY_S
        if isinstance(next_poll, dict):
            delay_session = db.make_session_factory(db.make_engine())()
            try:
                _, _, latest = _load_generation(delay_session, project_id, generation_id)
                due = _parse(latest.next_provider_check_at)
                if due:
                    delay = max(1, int((due - datetime.now(timezone.utc)).total_seconds()))
            finally:
                delay_session.close()
        generate_scene_video.apply_async(kwargs={
            "project_id": project_id,
            "generation_id": generation_id,
            "poll": next_poll,
        }, countdown=delay)
        return "polling"
    return state


@celery_app.task(name="app.workers.videos.recover_active_video_generations")
def recover_active_video_generations() -> int:
    session = db.make_session_factory(db.make_engine())()
    queued = 0
    try:
        for row in session.query(db.GenerationRow).all():
            data = row.data or {}
            if data.get("kind") != "video" or data.get("status") not in ACTIVE_STATUSES:
                continue
            due = _parse(data.get("next_provider_check_at"))
            if due and due > datetime.now(timezone.utc):
                continue
            generate_scene_video.delay(project_id=row.project_id,
                                       generation_id=row.generation_id)
            queued += 1
    finally:
        session.close()
    return queued


@worker_ready.connect
def _recover_on_worker_ready(sender=None, **kwargs):  # pragma: no cover - signal
    try:
        recover_active_video_generations.delay()
    except Exception:
        log.exception("failed to enqueue video recovery scan")
