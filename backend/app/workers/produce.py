"""/produce orchestration: stages 6→9 as a self-rescheduling Celery flow.

Gate respected: production refuses unless EVERY scene has an approved
(selected) image. Stage 6 fans out per-scene video tasks; this task then
polls readiness (non-blocking retry) and, once every scene has a selected
video, runs VO → music → render → final QC and stores final_render.
"""
import json
import uuid
from datetime import datetime, timezone

from app import db
from app.schema import AssetRef, Generation, Project
from app.snapshots import snapshot_project
from app.stages import audio as audio_stage
from app.stages import music as music_stage
from app.stages import qc, render
from app.storage import Storage
from app.workers.celery_app import celery_app
from app.workers.videos import MAX_ATTEMPTS, video_attempts

POLL_SECONDS = 20
MAX_POLLS = 240          # ~80 min ceiling for the whole video fan-out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(session, project: Project, reason: str) -> None:
    row = session.get(db.ProjectRow, project.project_id)
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="worker", reason=reason)


def readiness(project: Project) -> str:
    """'ready' | 'waiting' | 'stuck'."""
    waiting = False
    for scene in project.scenes:
        if scene.selected_video:
            continue
        active = any(g.kind == "video" and g.status in ("queued", "running")
                     for g in scene.generations)
        if active:
            waiting = True
        elif video_attempts(scene) >= MAX_ATTEMPTS:
            return "stuck"
        else:
            waiting = True   # retry task will re-enqueue via qc auto-retry
    return "waiting" if waiting else "ready"


def finalize(session, storage: Storage, project: Project) -> Project:
    """Stages 8 (VO + music) and 9 (render + final QC)."""
    ordered = sorted(project.scenes, key=lambda s: s.order)

    # ---- stage 8a: voiceover (one call, char timestamps) ----
    vo_mp3, alignment, vo_cost = audio_stage.synthesize(project.strategy.script)
    vo_asset_id = f"ast_{uuid.uuid4().hex[:12]}"
    vo_uri = storage.put_bytes(
        vo_mp3, f"{project.project_id}/audio/{vo_asset_id}.mp3", "audio/mpeg")
    align_id = f"ast_{uuid.uuid4().hex[:12]}"
    storage.put_bytes(json.dumps(alignment).encode(),
                      f"{project.project_id}/audio/{align_id}.json",
                      "application/json")
    project.voiceover = Generation(
        generation_id=f"gen_{uuid.uuid4().hex[:12]}",
        scene_id="", kind="audio", provider="elevenlabs",
        model=audio_stage.TTS_MODEL, prompt=project.strategy.script,
        prompt_hash="", reference_assets=[align_id], status="succeeded",
        cost_cents=vo_cost, created_at=_now(),
        asset=AssetRef(asset_id=vo_asset_id, kind="audio", uri=vo_uri,
                       created_at=_now()),
    )
    project.cost.voice += vo_cost
    _save(session, project, "produce:voiceover")
    session.commit()

    # ---- stage 8b: licensed music (library may legitimately be empty) ----
    music_mp3 = None
    tracks = music_stage.load_library(storage)
    total_s = sum(s.duration_s for s in ordered)
    try:
        track = music_stage.select_track(tracks, project.strategy, total_s)
        music_mp3 = storage.get_bytes(f"s3://{storage.bucket}/{track.audio_key}")
        project.music = AssetRef(
            asset_id=f"ast_{uuid.uuid4().hex[:12]}", kind="audio",
            uri=f"s3://{storage.bucket}/{track.audio_key}", created_at=_now())
        project.music_license = music_stage.license_for(track)
        _save(session, project, f"produce:music {track.track_id}")
        session.commit()
    except LookupError as exc:
        _save(session, project, f"produce:music skipped ({exc})")
        session.commit()

    # ---- stage 9: render + final QC ----
    clips: dict[str, bytes] = {}
    for scene in ordered:
        gen = next(g for g in scene.generations
                   if g.generation_id == scene.selected_video)
        clips[scene.scene_id] = storage.get_bytes(gen.asset.uri)
    final_mp4 = render.render(ordered, clips, vo_mp3, alignment, music_mp3)

    refs = [storage.get_bytes(a.uri)
            for a in project.product.reference_images[:qc.MAX_REFERENCES]]
    verdict, qc_cost = qc.run_qc(final_mp4, refs, ordered[0],
                                 captions_burned=True)
    project.cost.qc += qc_cost

    render_id = f"ast_{uuid.uuid4().hex[:12]}"
    uri = storage.put_bytes(
        final_mp4, f"{project.project_id}/renders/{render_id}.mp4", "video/mp4")
    project.final_render = AssetRef(
        asset_id=render_id, kind="render", uri=uri, created_at=_now())
    reason = ("produce:rendered" if verdict.passed
              else f"produce:rendered (final QC flagged: {verdict.notes})")
    _save(session, project, reason)
    session.commit()
    return project


@celery_app.task(bind=True, name="app.workers.produce.produce_project",
                 max_retries=MAX_POLLS)
def produce_project(self, project_id: str) -> str:
    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    try:
        row = session.get(db.ProjectRow, project_id)
        project = Project.model_validate(row.data)
        if project.final_render is not None:
            return "done"          # idempotent
        state = readiness(project)
        if state == "stuck":
            _save(session, project,
                  "produce:failed (a scene exhausted its video attempts)")
            session.commit()
            return "failed"
        if state == "waiting":
            raise self.retry(countdown=POLL_SECONDS)
        finalize(session, Storage(), project)
        return "done"
    finally:
        session.close()
