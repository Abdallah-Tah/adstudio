"""Stage 5 worker: one image generation per scene, retry cap 3 (absolute).

The Generation ledger is append-only: a new Generation row/entry per attempt,
never mutated after reaching a terminal status.
"""
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app import db
from app.compiler.gpt_image import (
    IMAGE_QUALITY,
    IMAGE_SIZE,
    compile_image_prompt,
)
from app.pricing import IMAGE_BASE_RATES
from app.providers import openai_images
from app.schema import AssetRef, Generation, Project
from app.snapshots import snapshot_project
from app.storage import Storage
from app.workers.celery_app import celery_app

MAX_ATTEMPTS = 3

log = logging.getLogger("adstudio.images")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttemptCapReached(Exception):
    pass


class ProviderNotConfigured(Exception):
    """Runtime preflight failure. Must consume NOTHING: no attempt, no
    Generation record, no queue entry, no cost."""
    error_code = "PROVIDER_NOT_CONFIGURED"


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


def start_generation(session, project: Project, scene_id: str) -> Generation:
    """Create the queued Generation + bump the attempt counter (cap-checked).

    Called from the API before enqueueing so the cap check and the attempt
    increment are atomic with the snapshot. preflight() must pass FIRST —
    a misconfigured provider consumes nothing.
    """
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    preflight(project, scene)
    compiled = compile_image_prompt(scene, project.strategy.style_id, project.product)
    generation = Generation(
        generation_id=f"gen_{uuid.uuid4().hex[:12]}",
        scene_id=scene_id,
        kind="image",
        provider=compiled.provider,
        model=compiled.model,
        prompt=compiled.prompt,
        prompt_hash=compiled.prompt_hash,
        reference_assets=compiled.reference_asset_ids,
        status="queued",
        created_at=_now(),
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
    return generation


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="worker", reason=reason)


def run_generation(session, storage: Storage, project_id: str, generation_id: str) -> str:
    """Execute one queued generation. Returns terminal status."""
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    scene = next(s for s in project.scenes
                 if any(g.generation_id == generation_id for g in s.generations))
    gen = next(g for g in scene.generations if g.generation_id == generation_id)
    if gen.status != "queued":
        log.info("skip %s: already %s (append-only)", generation_id, gen.status)
        return gen.status  # terminal statuses are immutable (append-only ledger)

    gen.status = "running"
    gen.started_at = _now()
    _save(session, project, f"generation running {generation_id}")
    session.commit()
    log.info("started generation=%s scene=%s model=%s", generation_id, scene.scene_id, gen.model)

    t0 = time.monotonic()
    try:
        by_id = {a.asset_id: a for a in project.product.reference_images}
        refs = [storage.get_bytes(by_id[aid].uri) for aid in gen.reference_assets]
        log.info("calling provider=%s model=%s refs=%d", gen.provider, gen.model, len(refs))
        png, cost = openai_images.generate_image(
            gen.prompt, refs, IMAGE_SIZE, IMAGE_QUALITY, gen.model
        )
        latency = round(time.monotonic() - t0, 1)
        log.info("received image gen=%s bytes=%d latency=%ss status=ok", generation_id, len(png), latency)
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        uri = storage.put_bytes(
            png, f"{project_id}/images/{asset_id}.png", "image/png")
        log.info("uploaded asset=%s gen=%s", asset_id, generation_id)
        gen.asset = AssetRef(
            asset_id=asset_id, kind="image", uri=uri,
            generated_from=scene.scene_id,
            reference_assets=gen.reference_assets, created_at=_now(),
        )
        gen.cost_cents = cost
        gen.status = "succeeded"
        project.cost.images += cost
    except SoftTimeLimitExceeded:
        gen.status = "failed"
        gen.qc_notes = "timed out (exceeded soft time limit)"
        log.warning("timeout gen=%s after %ss", generation_id, round(time.monotonic() - t0, 1))
    except Exception as exc:
        gen.status = "failed"
        gen.qc_notes = f"provider error: {type(exc).__name__}: {exc}"
        log.warning("failed gen=%s provider=%s error=%s: %s",
                    generation_id, gen.provider, type(exc).__name__, exc)

    _save(session, project, f"generation {gen.status} {generation_id}")
    log.info("completed gen=%s status=%s", generation_id, gen.status)
    gen_row = session.get(db.GenerationRow, generation_id)
    gen_row.data = gen.model_dump(mode="json")
    session.commit()
    return gen.status


@celery_app.task(name="app.workers.images.generate_scene_image")
def generate_scene_image(project_id: str, generation_id: str) -> str:
    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    try:
        return run_generation(session, Storage(), project_id, generation_id)
    finally:
        session.close()
