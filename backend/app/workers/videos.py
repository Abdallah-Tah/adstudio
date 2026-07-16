"""Stage 6 worker: one video clip per scene via fal Kling v3 Standard.

Rules:
- video generation NEVER runs without a selected image (hard rule);
- cap of 3 video attempts per scene is absolute (counted per kind);
- preflight failures consume nothing;
- the Celery task never blocks while fal renders — it re-schedules itself
  (retry with countdown) and completes idempotently.
"""
import os
import uuid
from datetime import datetime, timezone

from app import db
from app.compiler.kling_fal import (
    GENERATE_AUDIO,
    VIDEO_MODEL,
    compile_video_prompt,
    video_cost_cents,
)
from app.providers import fal_client
from app.schema import AssetRef, Generation, Project, Scene
from app.stages import qc
from app.snapshots import snapshot_project
from app.storage import Storage
from app.workers.celery_app import celery_app
from app.workers.images import AttemptCapReached, ProviderNotConfigured

MAX_ATTEMPTS = 3          # per kind per scene — absolute
POLL_SECONDS = 15
MAX_POLLS = 80            # ~20 min ceiling before we mark it failed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def video_attempts(scene: Scene) -> int:
    return sum(1 for g in scene.generations if g.kind == "video")


def preflight_video(project: Project, scene: Scene) -> int:
    """Runtime checks before anything is created. Returns estimated cents."""
    if not os.environ.get("FAL_KEY"):
        raise ProviderNotConfigured("FAL_KEY is not configured")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # QC is mandatory on every clip — don't spend on video we can't gate
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
    return video_cost_cents(scene.duration_s)


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="worker", reason=reason)


def start_video_generation(session, project: Project, scene_id: str) -> Generation:
    """Create the queued video Generation (preflight passes FIRST)."""
    scene = next(s for s in project.scenes if s.scene_id == scene_id)
    preflight_video(project, scene)
    compiled = compile_video_prompt(scene, project.strategy.style_id)
    start_gen = next(g for g in scene.generations
                     if g.generation_id == scene.selected_image)
    generation = Generation(
        generation_id=f"gen_{uuid.uuid4().hex[:12]}",
        scene_id=scene_id,
        kind="video",
        provider=compiled.provider,
        model=compiled.model,
        prompt=compiled.prompt,
        prompt_hash=compiled.prompt_hash,
        reference_assets=[start_gen.asset.asset_id],
        status="queued",
        created_at=_now(),
    )
    scene.generations.append(generation)
    _save(session, project,
          f"generate-video queued {generation.generation_id} "
          f"(attempt {video_attempts(scene)}/{MAX_ATTEMPTS})")
    session.add(db.GenerationRow(
        generation_id=generation.generation_id, project_id=project.project_id,
        scene_id=scene_id, data=generation.model_dump(mode="json"),
    ))
    session.commit()
    return generation


def _load_generation(session, project_id: str, generation_id: str):
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    scene = next(s for s in project.scenes
                 if any(g.generation_id == generation_id for g in s.generations))
    gen = next(g for g in scene.generations if g.generation_id == generation_id)
    return project, scene, gen


def run_video_step(
    session, storage: Storage, project_id: str, generation_id: str,
    poll: dict | None,
) -> tuple[str, dict | None]:
    """One non-blocking step of a video generation.

    Returns (state, poll_info): state is 'polling' (re-schedule me),
    'succeeded' or 'failed'. Terminal generations are never touched again."""
    project, scene, gen = _load_generation(session, project_id, generation_id)
    if gen.status in ("succeeded", "failed", "qc_rejected"):
        return gen.status, None    # idempotent completion (append-only ledger)

    try:
        if poll is None:
            # SUBMIT — build payload from the selected image + compiled prompt
            compiled = compile_video_prompt(scene, project.strategy.style_id)
            start_gen = next(g for g in scene.generations
                             if g.generation_id == scene.selected_image)
            image = storage.get_bytes(start_gen.asset.uri)
            poll = fal_client.submit(VIDEO_MODEL, {
                "prompt": compiled.prompt,
                "negative_prompt": compiled.negative_prompt,
                "start_image_url": fal_client.data_uri(image),
                "duration": str(compiled.billed_duration_s),
                "generate_audio": GENERATE_AUDIO,
            })
            gen.status = "running"
            _save(session, project, f"video generation submitted {generation_id} "
                                    f"(fal {poll['request_id']})")
            session.commit()
            return "polling", poll

        # POLL
        state = fal_client.status(poll["status_url"])
        if state in ("IN_QUEUE", "IN_PROGRESS"):
            return "polling", poll

        # COMPLETED — download, store, then QC-gate (stage 7)
        doc = fal_client.result(poll["response_url"])
        clip = fal_client.download(doc["video"]["url"])
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        uri = storage.put_bytes(
            clip, f"{project_id}/videos/{asset_id}.mp4", "video/mp4")
        gen.asset = AssetRef(
            asset_id=asset_id, kind="video", uri=uri,
            generated_from=scene.scene_id,
            reference_assets=gen.reference_assets, created_at=_now(),
        )
        gen.cost_cents = video_cost_cents(scene.duration_s)
        project.cost.videos += gen.cost_cents  # provider billed either way

        refs = [storage.get_bytes(a.uri)
                for a in project.product.reference_images[:qc.MAX_REFERENCES]]
        verdict, qc_cost = qc.run_qc(clip, refs, scene)
        project.cost.qc += qc_cost
        gen.qc_notes = verdict.notes
        gen.status = "succeeded" if verdict.passed else "qc_rejected"
        if gen.status == "succeeded" and scene.selected_video is None:
            # default selection so /produce can proceed; user can reselect
            scene.selected_video = gen.generation_id
    except Exception as exc:
        gen.status = "failed"
        gen.qc_notes = f"provider error: {exc}"

    _save(session, project, f"video generation {gen.status} {generation_id}")
    gen_row = session.get(db.GenerationRow, generation_id)
    gen_row.data = gen.model_dump(mode="json")
    session.commit()
    return gen.status, None


def maybe_autoretry_qc(session, project_id: str, generation_id: str) -> str | None:
    """After a qc_rejected clip: one automatic fresh attempt, but only while
    the absolute cap allows it. Returns the new generation_id or None."""
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    scene = next(s for s in project.scenes if any(
        g.generation_id == generation_id for g in s.generations))
    if video_attempts(scene) >= MAX_ATTEMPTS:
        return None
    retry = start_video_generation(session, project, scene.scene_id)
    generate_scene_video.delay(
        project_id=project_id, generation_id=retry.generation_id)
    return retry.generation_id


@celery_app.task(bind=True, name="app.workers.videos.generate_scene_video",
                 max_retries=MAX_POLLS)
def generate_scene_video(self, project_id: str, generation_id: str,
                         poll: dict | None = None) -> str:
    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    try:
        state, next_poll = run_video_step(
            session, Storage(), project_id, generation_id, poll)
        if state == "qc_rejected":
            maybe_autoretry_qc(session, project_id, generation_id)
    finally:
        session.close()
    if state == "polling":
        try:
            raise self.retry(countdown=POLL_SECONDS, kwargs={
                "project_id": project_id, "generation_id": generation_id,
                "poll": next_poll,
            })
        except self.MaxRetriesExceededError:
            # ~20 min without completion: mark failed rather than leaving a
            # permanent "running" record
            session = db.make_session_factory(db.make_engine())()
            try:
                project, _, gen = _load_generation(session, project_id, generation_id)
                if gen.status == "running":
                    gen.status = "failed"
                    gen.qc_notes = "provider timeout: no completion after 20 min"
                    _save(session, project, f"video generation failed {generation_id}")
                    gen_row = session.get(db.GenerationRow, generation_id)
                    gen_row.data = gen.model_dump(mode="json")
                    session.commit()
            finally:
                session.close()
            return "failed"
    return state
