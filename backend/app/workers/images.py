"""Stage 5 worker: one image generation per scene, retry cap 3 (absolute).

The Generation ledger is append-only: a new Generation row/entry per attempt,
never mutated after reaching a terminal status.
"""
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app import db
from app import generation_config
from app import product_composite
from app.compiler.gpt_image import (
    IMAGE_QUALITY,
    IMAGE_SIZE,
    compile_image_prompt,
)
from app.pricing import IMAGE_BASE_RATES
from app.providers import openai_images
from app.schema import AssetRef, Generation, Project
from app.snapshots import snapshot_project
from app.stages import image_identity_qc
from app.storage import Storage
from app.workers.celery_app import celery_app

MAX_ATTEMPTS = 3
AUTO_RETRY_DELAYS = (15, 45)
ACTIVE_STATUSES = {"queued", "running", "retrying"}
TERMINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled", "qc_rejected"}

log = logging.getLogger("adstudio.images")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttemptCapReached(Exception):
    pass


class ProviderNotConfigured(Exception):
    """Runtime preflight failure. Must consume NOTHING: no attempt, no
    Generation record, no queue entry, no cost."""
    error_code = "PROVIDER_NOT_CONFIGURED"


class RetryableGenerationError(Exception):
    pass


@dataclass(frozen=True)
class FailureInfo:
    error_code: str
    message: str
    transient: bool


@dataclass(frozen=True)
class GenerationStart:
    generation: Generation
    created: bool


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms_between(start: str | None, end: str | None) -> int | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if not a or not b:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _provider_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code.lower()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error", body)
        if isinstance(nested, dict) and isinstance(nested.get("code"), str):
            return nested["code"].lower()
    return ""


def _clean_message(exc: Exception) -> str:
    msg = " ".join(str(exc).split())
    return msg[:300] if msg else type(exc).__name__


def _qc_correction_prompt(prompt: str, gen: Generation) -> str:
    verdict = gen.identity_qc
    if verdict is None:
        return prompt
    defects = list(verdict.missing_parts) + list(verdict.invented_parts)
    if not defects and verdict.notes:
        defects = [verdict.notes[:160]]
    defects_text = "\n".join(f"- {d}" for d in defects[:6])
    return (
        f"{prompt}\n\n"
        "Previous generation failed identity validation:\n"
        f"{defects_text}\n"
        "Regenerate the same scene while correcting only those defects. "
        "Keep the scene composition unchanged and preserve the exact product."
    )


def _budget_allows(project: Project, estimated_cents: int) -> bool:
    budget = generation_config.PROJECT_BUDGET_CENTS
    return budget <= 0 or project.cost.total + estimated_cents <= budget


def classify_failure(exc: Exception) -> FailureInfo:
    """Classify provider failures without exposing raw provider payloads."""
    name = type(exc).__name__.lower()
    status = _status_code(exc)
    provider_code = _provider_code(exc)
    message = _clean_message(exc)
    lowered = message.lower()

    if isinstance(exc, SoftTimeLimitExceeded) or "timeout" in name:
        return FailureInfo("PROVIDER_TIMEOUT", "provider timeout", True)
    if "connection" in name or "network" in name:
        return FailureInfo("PROVIDER_NETWORK_ERROR", "temporary network failure", True)
    if status == 429:
        if "insufficient" in provider_code or "quota" in lowered or "balance" in lowered:
            return FailureInfo("INSUFFICIENT_BALANCE", "insufficient account balance", False)
        return FailureInfo("RATE_LIMIT", "rate limit", True)
    if status in (500, 502, 503, 504):
        return FailureInfo("PROVIDER_UNAVAILABLE", "provider temporarily unavailable", True)
    if status in (401, 403) or "authentication" in name or "permission" in name:
        return FailureInfo("INVALID_API_KEY", "invalid API key", False)
    if status == 400 or "badrequest" in name or "invalidrequest" in name:
        if "content" in lowered and ("policy" in lowered or "safety" in lowered):
            return FailureInfo("OPENAI_REQUEST_REJECTED", "OpenAI request rejected", False)
        if "image" in lowered:
            return FailureInfo("INVALID_REFERENCE_IMAGE", "invalid reference image", False)
        if "model" in lowered:
            return FailureInfo("UNSUPPORTED_MODEL", "unsupported model", False)
        return FailureInfo("INVALID_REQUEST", "invalid request", False)
    return FailureInfo("PROVIDER_ERROR", message, False)


def preflight(project: Project, scene) -> int:
    """Runtime checks before anything is created or enqueued.
    Returns the estimated cost in cents."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ProviderNotConfigured("OPENAI_API_KEY is not configured")
    if IMAGE_QUALITY not in IMAGE_BASE_RATES:
        raise ProviderNotConfigured(f"unsupported image quality {IMAGE_QUALITY!r}")
    ref_ids = {a.asset_id for a in project.product.reference_images}
    compiled = compile_image_prompt(scene, project.strategy.style_id, project.product)
    if not compiled.reference_asset_ids or not set(compiled.reference_asset_ids) <= ref_ids:
        raise ProviderNotConfigured("reference images are not accessible")
    if scene.generation_attempts >= MAX_ATTEMPTS:
        raise AttemptCapReached(
            f"scene {scene.scene_id} already used "
            f"{scene.generation_attempts}/{MAX_ATTEMPTS} image attempts"
        )
    return openai_images.image_cost_cents(IMAGE_SIZE, IMAGE_QUALITY)


def find_active_generation(project: Project, scene_id: str) -> Generation | None:
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    prompt_hash = compile_image_prompt(
        scene, project.strategy.style_id, project.product).prompt_hash
    return next((
        g for g in reversed(scene.generations)
        if g.kind == "image"
        and g.status in ACTIVE_STATUSES
        and g.prompt_hash == prompt_hash
    ), None)


def start_generation(session, project: Project, scene_id: str) -> GenerationStart:
    """Create the queued Generation + bump the attempt counter (cap-checked).

    Called from the API before enqueueing so the cap check and the attempt
    increment are atomic with the snapshot. preflight() must pass FIRST —
    a misconfigured provider consumes nothing.
    """
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    compiled = compile_image_prompt(scene, project.strategy.style_id, project.product)
    active = find_active_generation(project, scene_id)
    if active is not None:
        return GenerationStart(active, False)
    preflight(project, scene)
    now = _now()
    generation = Generation(
        generation_id=f"gen_{uuid.uuid4().hex[:12]}",
        scene_id=scene_id,
        kind="image",
        provider=compiled.provider,
        model=compiled.model,
        prompt=compiled.prompt,
        prompt_hash=compiled.prompt_hash,
        reference_assets=compiled.reference_asset_ids,
        reference_types=compiled.reference_types,
        generation_mode=compiled.generation_mode,
        status="queued",
        created_at=now,
        queued_at=now,
        attempt_number=1,
    )
    scene.generations.append(generation)
    scene.generation_attempts += 1
    _save(session, project, f"generate-image queued {generation.generation_id} "
                            f"(attempt {scene.generation_attempts}/{MAX_ATTEMPTS})")
    session.add(db.GenerationRow(
        generation_id=generation.generation_id, project_id=project.project_id,
        scene_id=scene_id, data=generation.model_dump(mode="json"),
    ))
    session.commit()
    return GenerationStart(generation, True)


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="worker", reason=reason)


def _cancelled(session, generation_id: str) -> bool:
    row = session.get(db.GenerationRow, generation_id)
    return bool(row and (row.data or {}).get("status") == "cancelled")


def run_generation(
    session,
    storage: Storage,
    project_id: str,
    generation_id: str,
    *,
    provider_attempt: int = 1,
    final_attempt: bool = True,
) -> str:
    """Execute one queued generation. Returns terminal status."""
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    scene = next(s for s in project.scenes
                 if any(g.generation_id == generation_id for g in s.generations))
    gen = next(g for g in scene.generations if g.generation_id == generation_id)
    if gen.status not in ("queued", "retrying"):
        log.info("skip %s: already %s (append-only)", generation_id, gen.status)
        return gen.status  # terminal statuses are immutable (append-only ledger)

    started_at = _now()
    gen.status = "running"
    gen.started_at = gen.started_at or started_at
    gen.attempt_number = provider_attempt
    gen.queue_wait_ms = _ms_between(gen.queued_at or gen.created_at, gen.started_at)
    gen.error_code = None
    gen.error_message = None
    gen.qc_notes = None
    _save(session, project, f"generation running {generation_id}")
    session.commit()
    log.info("started generation=%s scene=%s model=%s attempt=%s",
             generation_id, scene.scene_id, gen.model, provider_attempt)

    provider_t0 = time.monotonic()
    try:
        by_id = {a.asset_id: a for a in project.product.reference_images}
        refs = [storage.get_bytes(by_id[aid].uri) for aid in gen.reference_assets]
        cutout_ref = next((
            refs[i] for i, ref_type in enumerate(gen.reference_types)
            if ref_type == "cutout" and i < len(refs)
        ), None)
        gen.provider_called_at = _now()
        gen_row = session.get(db.GenerationRow, generation_id)
        gen_row.data = gen.model_dump(mode="json")
        _save(session, project, f"generation provider call {generation_id}")
        session.commit()
        log.info("calling provider=%s model=%s refs=%d", gen.provider, gen.model, len(refs))
        image_prompt = gen.prompt
        image_cost_total = 0
        qc_cost_total = 0
        identity_retry_used = False
        while True:
            png, cost = openai_images.generate_image(
                image_prompt, refs, IMAGE_SIZE, IMAGE_QUALITY, gen.model
            )
            image_cost_total += cost
            if gen.generation_mode == "composite_exact_product" and cutout_ref is not None:
                png = product_composite.composite_exact_product(png, cutout_ref)
            if not generation_config.IMAGE_IDENTITY_QC_ENABLED:
                break
            qc_verdict, qc_cost = image_identity_qc.run_qc(
                png, refs, scene, project.product)
            qc_cost_total += qc_cost
            gen.identity_qc = qc_verdict
            gen.qc_notes = qc_verdict.notes
            log.info("image identity qc gen=%s score=%.2f severe=%s",
                     generation_id, qc_verdict.identity_score, qc_verdict.severe_failure)
            if not image_identity_qc.should_reject(qc_verdict):
                break
            retry_cost = openai_images.image_cost_cents(IMAGE_SIZE, IMAGE_QUALITY)
            if (
                generation_config.AUTO_RETRY_IMAGES
                and not identity_retry_used
                and _budget_allows(project, retry_cost)
            ):
                identity_retry_used = True
                image_prompt = _qc_correction_prompt(gen.prompt, gen)
                continue
            gen.status = "qc_rejected"
            gen.error_code = "QC_REJECTED"
            gen.error_message = qc_verdict.notes or "product identity mismatch"
            gen.finished_at = _now()
            gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
            gen.cost_cents = image_cost_total
            project.cost.images += image_cost_total
            project.cost.qc += qc_cost_total
            _save(session, project, f"generation qc rejected {generation_id}")
            gen_row = session.get(db.GenerationRow, generation_id)
            gen_row.data = gen.model_dump(mode="json")
            session.commit()
            return gen.status
        gen.provider_completed_at = _now()
        gen.provider_latency_ms = int((time.monotonic() - provider_t0) * 1000)
        latency = round(gen.provider_latency_ms / 1000, 1)
        log.info("received image gen=%s bytes=%d latency=%ss status=ok", generation_id, len(png), latency)
        if _cancelled(session, generation_id):
            log.info("discarding provider result for cancelled gen=%s", generation_id)
            return "cancelled"
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        upload_t0 = time.monotonic()
        uri = storage.put_bytes(
            png, f"{project_id}/images/{asset_id}.png", "image/png")
        gen.asset_uploaded_at = _now()
        gen.upload_latency_ms = int((time.monotonic() - upload_t0) * 1000)
        log.info("uploaded asset=%s gen=%s", asset_id, generation_id)
        gen.asset = AssetRef(
            asset_id=asset_id, kind="image", uri=uri,
            generated_from=scene.scene_id,
            reference_assets=gen.reference_assets, created_at=_now(),
        )
        gen.cost_cents = image_cost_total
        gen.status = "succeeded"
        gen.finished_at = _now()
        gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
        project.cost.images += image_cost_total
        project.cost.qc += qc_cost_total
    except Exception as exc:
        failure = classify_failure(exc)
        if gen.provider_called_at and gen.provider_completed_at is None:
            gen.provider_latency_ms = int((time.monotonic() - provider_t0) * 1000)
        gen.error_code = failure.error_code
        gen.error_message = failure.message
        gen.qc_notes = failure.message
        if failure.transient and not final_attempt:
            gen.status = "retrying"
            _save(session, project, f"generation retrying {generation_id} {failure.error_code}")
            log.warning("retrying gen=%s provider=%s error_code=%s attempt=%s",
                        generation_id, gen.provider, failure.error_code, provider_attempt)
            gen_row = session.get(db.GenerationRow, generation_id)
            gen_row.data = gen.model_dump(mode="json")
            session.commit()
            raise RetryableGenerationError(failure.error_code) from exc
        gen.status = "failed"
        gen.finished_at = _now()
        gen.total_latency_ms = _ms_between(gen.queued_at or gen.created_at, gen.finished_at)
        log.warning("failed gen=%s provider=%s error_code=%s transient=%s",
                    generation_id, gen.provider, failure.error_code, failure.transient)

    _save(session, project, f"generation {gen.status} {generation_id}")
    log.info("completed gen=%s status=%s", generation_id, gen.status)
    gen_row = session.get(db.GenerationRow, generation_id)
    gen_row.data = gen.model_dump(mode="json")
    session.commit()
    return gen.status


@celery_app.task(bind=True, name="app.workers.images.generate_scene_image",
                 max_retries=len(AUTO_RETRY_DELAYS))
def generate_scene_image(self, project_id: str, generation_id: str) -> str:
    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    try:
        provider_attempt = self.request.retries + 1
        final_attempt = self.request.retries >= len(AUTO_RETRY_DELAYS)
        return run_generation(
            session, Storage(), project_id, generation_id,
            provider_attempt=provider_attempt, final_attempt=final_attempt)
    except RetryableGenerationError as exc:
        countdown = AUTO_RETRY_DELAYS[self.request.retries]
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        session.close()
