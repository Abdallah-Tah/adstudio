"""Auto-pilot: drives the existing gated pipeline end-to-end.

Customers who pick "auto" hand the build to the AI after the upload quality
gate. Auto-pilot never bypasses a gate — it drives the same primitives in the
same order a careful user would (approved storyboard -> per-scene images ->
identity-QC auto-select -> cross-scene consistency -> produce) and drops to
`needs_review` the moment something needs a human decision. Manual mode and
the scene editor are untouched; a paused/needs_review project is just a
normal project mid-flow.
"""
import logging
from datetime import datetime, timezone

from app import db, generation_config
from app import production_readiness
from app.schema import Generation, Project, Scene
from app.snapshots import snapshot_project
from app.stages import scene_consistency
from app.storage import Storage
from app.workers import produce as produce_worker
from app.workers import images as image_worker
from app.workers import videos as video_worker
from app.workers.celery_app import celery_app

POLL_SECONDS = 12
MAX_TICKS = 600                 # ~2h ceiling before giving the project back
TERMINAL = {"completed", "failed", "needs_review"}

log = logging.getLogger("adstudio.autopilot")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="autopilot", reason=reason)
    session.commit()


def _set(project: Project, status: str, detail: str = "") -> None:
    project.automation.status = status
    project.automation.detail = detail
    project.automation.updated_at = _now()


def _passing_image(scene: Scene) -> Generation | None:
    """Newest succeeded image that identity QC did not reject."""
    for gen in reversed(scene.generations):
        if (gen.kind == "image" and gen.status == "succeeded" and gen.asset
                and (gen.identity_qc is None or gen.identity_qc.passed)):
            return gen
    return None


def _scene_label(project: Project, scene: Scene) -> str:
    return f"Scene {scene.order + 1}"


def _images_step(session, project: Project) -> str:
    """Queue missing images, auto-select QC-passed ones. Returns
    'ready' | 'waiting' | 'needs_review'."""
    waiting = False
    queued_generation_ids: list[str] = []
    for scene_id in [s.scene_id for s in project.scenes]:
        row = session.get(db.ProjectRow, project.project_id)
        current = Project.model_validate(row.data)
        current.automation = project.automation
        scene = next(s for s in current.scenes if s.scene_id == scene_id)
        if scene.selected_image:
            continue
        passing = _passing_image(scene)
        if passing is not None:
            scene.selected_image = passing.generation_id
            _save(session, current,
                  f"autopilot selected image {passing.generation_id} "
                  f"for {scene.scene_id}")
            continue
        active = any(
            g.kind == "image" and g.status in image_worker.ACTIVE_STATUSES
            for g in scene.generations
        )
        if active:
            waiting = True
            continue
        if scene.generation_attempts >= image_worker.MAX_ATTEMPTS:
            _set(project, "needs_review",
                 f"{_scene_label(current, scene)} used all "
                 f"{image_worker.MAX_ATTEMPTS} image attempts without an "
                 "identity-approved result. Open the editor to adjust the "
                 "scene or pick an image manually.")
            return "needs_review"
        try:
            started = image_worker.start_generation(session, current, scene_id)
        except image_worker.ProviderNotConfigured as exc:
            _set(project, "needs_review", str(exc))
            return "needs_review"
        except image_worker.AttemptCapReached as exc:
            _set(project, "needs_review", str(exc))
            return "needs_review"
        if started.created:
            # Commit every generation to the project ledger before a worker can
            # consume any of them. Dispatching inside this loop lets a fast
            # worker save an older JSON snapshot while later scenes are still
            # being appended, erasing those generation IDs.
            queued_generation_ids.append(started.generation.generation_id)
        waiting = True

    for generation_id in queued_generation_ids:
        image_worker.generate_scene_image.delay(project.project_id, generation_id)

    row = session.get(db.ProjectRow, project.project_id)
    final = Project.model_validate(row.data)
    if all(s.selected_image for s in final.scenes):
        return "ready"
    return "waiting"


def _video_retry_step(session, project: Project) -> str | None:
    """During production: re-kick scenes whose video was rejected by QC.

    Auto mode is explicit consent for hands-free spend inside the absolute
    3-attempt cap, so QC-rejected clips retry without a human click."""
    for scene in project.scenes:
        if scene.selected_video:
            continue
        active = any(g.kind == "video" and g.status in video_worker.ACTIVE_STATUSES
                     for g in scene.generations)
        rejected = any(g.kind == "video" and g.status == "qc_rejected"
                       for g in scene.generations)
        if active or not rejected:
            continue
        if video_worker.video_attempts(scene) >= video_worker.MAX_ATTEMPTS:
            _set(project, "needs_review",
                 f"{_scene_label(project, scene)} used all video attempts "
                 "without passing product-identity QC. Pick a video manually "
                 "or override QC in the editor.")
            return "needs_review"
        try:
            started = video_worker.start_video_generation(
                session, project, scene.scene_id)
        except (image_worker.ProviderNotConfigured,
                image_worker.AttemptCapReached, ValueError) as exc:
            _set(project, "needs_review", str(exc))
            return "needs_review"
        if started.created:
            video_worker.generate_scene_video.delay(
                project_id=project.project_id,
                generation_id=started.generation.generation_id)
    return None


def tick(session, storage: Storage, project_id: str) -> str:
    """One auto-pilot step. Returns the automation status after the step."""
    row = session.get(db.ProjectRow, project_id)
    project = Project.model_validate(row.data)
    auto = project.automation
    if auto.mode != "auto" or auto.status in TERMINAL:
        return auto.status

    if project.final_render is not None:
        _set(project, "completed", "Your ad is ready.")
        _save(session, project, "autopilot completed")
        return "completed"

    if project.storyboard_approval.status != "approved":
        _set(project, "needs_review",
             "The storyboard was edited during the auto build. Review and "
             "approve it to resume.")
        _save(session, project, "autopilot needs review (storyboard draft)")
        return "needs_review"

    if not all(s.selected_image for s in project.scenes):
        if auto.status != "generating_images":
            _set(project, "generating_images",
                 "Generating and quality-checking a still for every scene.")
            _save(session, project, "autopilot generating images")
        state = _images_step(session, project)
        # _images_step saved scene selections row-by-row; reload before any
        # further save so we never overwrite them with this stale copy.
        row = session.get(db.ProjectRow, project_id)
        fresh = Project.model_validate(row.data)
        fresh.automation = project.automation
        project = fresh
        if state == "needs_review":
            _save(session, project, "autopilot needs review (images)")
            return "needs_review"
        if state == "waiting":
            return "generating_images"

    if (generation_config.SCENE_CONSISTENCY_QC_ENABLED
            and not scene_consistency.is_current(project)):
        _set(project, "checking_consistency",
             "Comparing all scenes against your product photos.")
        _save(session, project, "autopilot checking consistency")
        try:
            report, cost = scene_consistency.run_check(project, storage)
        except Exception as exc:
            _set(project, "needs_review",
                 f"Cross-scene identity check could not run: {exc}")
            _save(session, project, "autopilot needs review (consistency)")
            return "needs_review"
        project.scene_consistency = report
        project.cost.qc += cost
        _save(session, project,
              f"autopilot consistency "
              f"({'consistent' if report.consistent else 'inconsistent'})")
    if (project.scene_consistency is not None
            and scene_consistency.is_current(project)
            and not project.scene_consistency.consistent):
        bad = [v for v in project.scene_consistency.verdicts if not v.consistent]
        scenes = ", ".join(str(next(
            (s.order + 1 for s in project.scenes if s.scene_id == v.scene_id), "?"
        )) for v in bad)
        _set(project, "needs_review",
             f"The product looks different in scene(s) {scenes}. Regenerate "
             "those images in the editor, then resume auto-pilot.")
        _save(session, project, "autopilot needs review (inconsistent scenes)")
        return "needs_review"

    job = production_readiness.active_job(project)
    if job is None:
        if project.production_job and project.production_job.status == "failed":
            # A production poll can observe a scene before its final queued
            # video completion has committed. Re-check the authoritative
            # project state before telling the customer Auto-pilot failed.
            # If all late completions are now selected, safely start a fresh
            # idempotent production job instead of stranding a valid project.
            if project.production_job.error_code == "VIDEO_ATTEMPTS_EXHAUSTED":
                readiness = production_readiness.validate(project)
                if readiness.ready:
                    new_job = production_readiness.new_job(project)
                    project.production_job = new_job
                    _set(project, "producing",
                         "All approved scene clips are ready. Resuming final production.")
                    _save(session, project,
                          f"autopilot recovered production {new_job.production_job_id}")
                    produce_worker.produce_project.delay(project.project_id)
                    return "producing"

                retry_state = _video_retry_step(session, project)
                if retry_state == "needs_review":
                    _save(session, project, "autopilot needs review (video cap)")
                    return "needs_review"
                # A retry was queued after the failed poll. Give it a fresh
                # production job to watch rather than leaving a terminal one.
                new_job = production_readiness.new_job(project)
                project.production_job = new_job
                _set(project, "producing", "Retrying scene videos after quality review.")
                _save(session, project,
                      f"autopilot resumed video production {new_job.production_job_id}")
                produce_worker.produce_project.delay(project.project_id)
                return "producing"
            _set(project, "failed",
                 project.production_job.error_message or "Production failed.")
            _save(session, project, "autopilot failed (production)")
            return "failed"
        readiness = production_readiness.validate(project)
        if not readiness.ready:
            first = readiness.blocking_reasons[0]
            _set(project, "needs_review", first.message)
            _save(session, project, "autopilot needs review (not ready)")
            return "needs_review"
        new_job = production_readiness.new_job(project)
        project.production_job = new_job
        _set(project, "producing",
             "Generating videos, voiceover, and the final render.")
        _save(session, project, f"autopilot production queued "
                                f"{new_job.production_job_id}")
        for scene in project.scenes:
            if scene.selected_video:
                continue
            try:
                started = video_worker.start_video_generation(
                    session, project, scene.scene_id)
            except (image_worker.ProviderNotConfigured,
                    image_worker.AttemptCapReached, ValueError) as exc:
                _set(project, "needs_review", str(exc))
                _save(session, project, "autopilot needs review (video start)")
                return "needs_review"
            if started.created:
                video_worker.generate_scene_video.delay(
                    project_id=project.project_id,
                    generation_id=started.generation.generation_id)
        produce_worker.produce_project.delay(project.project_id)
        return "producing"

    state = _video_retry_step(session, project)
    if state == "needs_review":
        _save(session, project, "autopilot needs review (video retries)")
        return "needs_review"
    if auto.status != "producing":
        _set(project, "producing",
             "Generating videos, voiceover, and the final render.")
        _save(session, project, "autopilot producing")
    return "producing"


@celery_app.task(bind=True, name="app.workers.autopilot.run_autopilot",
                 max_retries=MAX_TICKS)
def run_autopilot(self, project_id: str) -> str:
    session = db.make_session_factory(db.make_engine())()
    try:
        try:
            state = tick(session, Storage(), project_id)
        except Exception as exc:  # never leave the customer without a status
            log.exception("autopilot tick crashed project=%s", project_id)
            row = session.get(db.ProjectRow, project_id)
            if row is not None:
                project = Project.model_validate(row.data)
                _set(project, "failed", str(exc).splitlines()[0][:240])
                _save(session, project, "autopilot failed (crash)")
            return "failed"
    finally:
        session.close()
    if state in TERMINAL:
        return state
    if self.request.retries >= MAX_TICKS - 1:
        return state
    raise self.retry(countdown=POLL_SECONDS)
