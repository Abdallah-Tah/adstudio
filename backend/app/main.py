"""FastAPI entry point. Phase 1: schema export, project creation, scene edits."""
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import db
from app.compiler.gpt_image import compile_image_prompt
from app.pipeline import create_project
from app.schema import Project, StoryboardApproval
from app.snapshots import snapshot_project
from app.stages.brief import UserInputs
from app.stages.storyboard import StoryboardValidationError
from app.storage import Storage
from app.workers import images as image_worker
from app.workers import produce as produce_worker
from app.workers import videos as video_worker

_session_factory = None


def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = db.make_session_factory()
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_storage() -> Storage:
    return Storage()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="AI Ad Studio", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # editor v0, single-user localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]


def enriched(project: Project) -> dict:
    """Project JSON with computed (not stored) staleness on image generations:
    a generation is stale when its prompt_hash no longer matches the hash of
    the prompt compiled from the scene's current intent."""
    from app.compiler import engines

    doc = project.model_dump(mode="json")
    for scene, scene_doc in zip(project.scenes, doc["scenes"]):
        current_img = compile_image_prompt(
            scene, project.strategy.style_id, project.product
        ).prompt_hash
        for gen_doc in scene_doc["generations"]:
            if gen_doc["kind"] == "image":
                gen_doc["stale"] = gen_doc["prompt_hash"] != current_img
            elif scene.selected_image:
                # recompute against the engine that produced this video, so a
                # video is stale only when the scene intent actually changed
                engine = engines.by_model(gen_doc["model"])
                current_vid = (engine.compile_video_prompt(
                    scene, project.strategy.style_id).prompt_hash
                    if engine else None)
                gen_doc["stale"] = (current_vid is not None
                                    and gen_doc["prompt_hash"] != current_vid)
            else:
                gen_doc["stale"] = False
    doc["cost"]["total"] = project.cost.total
    return doc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/schema")
def schema() -> dict:
    return Project.model_json_schema()


@app.post("/projects")
async def post_project(
    session: SessionDep,
    storage: StorageDep,
    photos: list[UploadFile],
    description: Annotated[str, Form()],
    audience: Annotated[Optional[str], Form()] = None,
    offer: Annotated[Optional[str], Form()] = None,
    cta: Annotated[Optional[str], Form()] = None,
    tone: Annotated[Optional[str], Form()] = None,
    style: Annotated[Optional[str], Form()] = None,
    target_duration_s: Annotated[Optional[float], Form()] = None,
) -> dict:
    if not photos:
        raise HTTPException(422, "at least one photo is required")
    user = UserInputs(
        audience=audience, offer=offer, cta=cta, tone=tone,
        style=style, target_duration_s=target_duration_s,
    )
    payloads = [(p.filename or "photo.jpg", await p.read()) for p in photos]
    try:
        return enriched(create_project(session, storage, payloads, description, user))
    except StoryboardValidationError as exc:
        raise HTTPException(422, {
            "error_code": StoryboardValidationError.error_code,
            "detail": str(exc),
            "cost_cents": exc.cost_cents,
        })
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _load_project(session: Session, project_id: str) -> tuple[db.ProjectRow, Project]:
    row = session.get(db.ProjectRow, project_id)
    if row is None:
        raise HTTPException(404, f"project {project_id} not found")
    return row, Project.model_validate(row.data)


def _listing_thumb(scenes: list[dict]) -> str | None:
    for s in scenes:
        gens = s.get("generations", [])
        gen = next((g for g in gens if g["generation_id"] == s.get("selected_image")),
                   None) or next(
            (g for g in reversed(gens) if g["status"] == "succeeded"), None)
        if gen and gen.get("asset"):
            return gen["asset"]["asset_id"]
    return None


def _listing_status(data: dict) -> str:
    scenes = data["scenes"]
    if data.get("final_render"):
        return "video_ready"
    if all(s.get("selected_image") for s in scenes):
        return "images_ready"
    if any(g["status"] in ("queued", "running")
           for s in scenes for g in s.get("generations", [])):
        return "running"
    if any(s.get("selected_image") for s in scenes):
        return "image_ready"
    return "storyboard"


@app.get("/projects")
def list_projects(session: SessionDep) -> list[dict]:
    rows = session.query(db.ProjectRow).order_by(db.ProjectRow.created_at.desc()).all()
    from sqlalchemy import func
    latest = dict(
        session.query(db.ProjectVersionRow.project_id,
                      func.max(db.ProjectVersionRow.created_at))
        .group_by(db.ProjectVersionRow.project_id).all()
    )
    return [
        {"project_id": r.project_id, "name": r.data["product"]["name"],
         "scenes": len(r.data["scenes"]), "created_at": r.data["created_at"],
         "cost_cents": sum(r.data["cost"].values()),
         "status": _listing_status(r.data),
         "thumb_asset_id": _listing_thumb(r.data["scenes"]),
         "updated_at": str(latest.get(r.project_id, r.data["created_at"]))}
        for r in rows
    ]


@app.get("/activity")
def activity(session: SessionDep) -> list[dict]:
    """Most recent project mutations — the dashboard activity feed."""
    rows = (
        session.query(db.ProjectVersionRow)
        .order_by(db.ProjectVersionRow.created_at.desc())
        .limit(12).all()
    )
    names: dict[str, str] = {}
    for v in rows:
        if v.project_id not in names:
            p = session.get(db.ProjectRow, v.project_id)
            names[v.project_id] = (
                p.data["product"]["name"] if p else v.project_id)
    return [
        {"project_id": v.project_id, "project_name": names[v.project_id],
         "reason": v.reason, "actor": v.actor, "created_at": str(v.created_at)}
        for v in rows
    ]


@app.get("/providers")
def providers() -> list[dict]:
    """Read-only provider connection status (from backend-only env config)."""
    import os

    from app.compiler import engines
    from app.compiler.gpt_image import IMAGE_MODEL
    from app.providers.openai_client import STAGE_MODEL

    def ok(key: str) -> bool:
        return bool(os.environ.get(key))

    active = engines.active_name()
    return [
        {"id": "openai", "name": "OpenAI",
         "role": "Product analysis, strategy, storyboard + image generation",
         "models": [STAGE_MODEL, IMAGE_MODEL], "connected": ok("OPENAI_API_KEY")},
        {"id": "fal", "name": "fal.ai",
         "role": f"Video generation (Phase 3) — active engine: {active}, audio off",
         "models": [m.VIDEO_MODEL for m in engines.ENGINES.values()],
         "active_model": engines.active_engine().VIDEO_MODEL,
         "connected": ok("FAL_KEY")},
        {"id": "anthropic", "name": "Anthropic",
         "role": "Vision QC verdicts on generated clips (Phase 3)",
         "models": [], "connected": ok("ANTHROPIC_API_KEY")},
        {"id": "elevenlabs", "name": "ElevenLabs",
         "role": "Voiceover from the script (Phase 3)",
         "models": [], "connected": ok("ELEVENLABS_API_KEY")},
        {"id": "music", "name": "Music library",
         "role": "Licensed stock catalog with stored license IDs (Phase 3)",
         "models": [], "connected": ok("MUSIC_LIBRARY_KEY")},
    ]


class ProviderKeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


@app.post("/providers/{provider_id}/key")
def set_provider_key(provider_id: str, body: ProviderKeyBody) -> dict:
    """Store a provider API key (write-only — the key is never returned)."""
    from app import settings_store

    if provider_id not in settings_store.PROVIDER_KEYS:
        raise HTTPException(404, f"unknown provider {provider_id}")
    settings_store.set_key(provider_id, body.api_key.strip())
    return {"id": provider_id, "connected": True,
            "note": "applied to the API immediately; restart the worker to "
                    "pick it up there"}


@app.delete("/providers/{provider_id}/key")
def clear_provider_key(provider_id: str) -> dict:
    from app import settings_store

    if provider_id not in settings_store.PROVIDER_KEYS:
        raise HTTPException(404, f"unknown provider {provider_id}")
    settings_store.clear_key(provider_id)
    return {"id": provider_id, "connected": False}


@app.get("/projects/{project_id}")
def get_project(project_id: str, session: SessionDep) -> dict:
    return enriched(_load_project(session, project_id)[1])


@app.get("/projects/{project_id}/versions")
def get_versions(project_id: str, session: SessionDep) -> list[dict]:
    rows = (
        session.query(db.ProjectVersionRow)
        .filter_by(project_id=project_id)
        .order_by(db.ProjectVersionRow.created_at)
        .all()
    )
    if not rows:
        raise HTTPException(404, f"no versions for project {project_id}")
    return [
        {
            "version_id": r.version_id,
            "actor": r.actor,
            "reason": r.reason,
            "created_at": str(r.created_at),
            "snapshot": r.snapshot,
        }
        for r in rows
    ]


class ScenePatch(BaseModel):
    """Intent fields only — nothing else is editable here."""
    order: Optional[int] = None
    duration_s: Optional[float] = Field(default=None, ge=0.5, le=8.0)
    camera: Optional[str] = None
    lighting: Optional[str] = None
    action: Optional[str] = None
    vo_line: Optional[str] = None
    caption: Optional[str] = None
    caption_style: Optional[Literal["bounce", "highlight", "plain"]] = None
    transition_out: Optional[Literal["cut", "fade", "whip"]] = None


@app.post("/projects/{project_id}/scenes/{scene_id}")
def patch_scene(
    project_id: str, scene_id: str, patch: ScenePatch, session: SessionDep
) -> dict:
    row, project = _load_project(session, project_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(422, "empty patch")
    idx = project.scenes.index(scene)
    project.scenes[idx] = scene.model_copy(update=updates)
    # editing intent after approval returns the storyboard to draft
    reverted = project.storyboard_approval.status == "approved"
    if reverted:
        project.storyboard_approval = StoryboardApproval()
    try:
        project = Project.model_validate(project.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(422, str(exc))
    row.data = project.model_dump(mode="json")
    reason = f"scene edit {scene_id}: {sorted(updates)}"
    if reverted:
        reason += " (storyboard approval reverted to draft)"
    snapshot_project(session, project, actor="user", reason=reason)
    session.commit()
    return enriched(project)


@app.post("/projects/{project_id}/storyboard/approve")
def approve_storyboard(project_id: str, session: SessionDep) -> dict:
    """Explicit storyboard approval — the gate for batch image generation."""
    from datetime import datetime, timezone

    row, project = _load_project(session, project_id)
    project.storyboard_approval = StoryboardApproval(
        status="approved",
        approved_at=datetime.now(timezone.utc).isoformat(),
        approved_by="user",
    )
    version = snapshot_project(session, project, actor="user",
                               reason="storyboard approved")
    project.storyboard_approval.approved_version_id = version.version_id
    row.data = project.model_dump(mode="json")
    session.commit()
    return enriched(project)


@app.post("/projects/{project_id}/generate-images")
def generate_all_images(project_id: str, session: SessionDep) -> dict:
    """Batch generation for every scene without a selected image.
    Requires explicit storyboard approval (single-scene generate is the
    unapproved preview path)."""
    _, project = _load_project(session, project_id)
    if project.storyboard_approval.status != "approved":
        raise HTTPException(409, {
            "error_code": "STORYBOARD_NOT_APPROVED",
            "detail": "batch image generation requires explicit storyboard approval",
        })
    queued, skipped = [], []
    for scene_id in [s.scene_id for s in project.scenes]:
        # reload each iteration: workers may have mutated the row in between
        _, current = _load_project(session, project_id)
        scene = next(s for s in current.scenes if s.scene_id == scene_id)
        if scene.selected_image:
            skipped.append({"scene_id": scene.scene_id, "reason": "image selected"})
            continue
        try:
            generation = image_worker.start_generation(session, current, scene.scene_id)
        except image_worker.AttemptCapReached as exc:
            skipped.append({"scene_id": scene.scene_id, "reason": str(exc)})
            continue
        except image_worker.ProviderNotConfigured as exc:
            raise HTTPException(409, {
                "error_code": image_worker.ProviderNotConfigured.error_code,
                "detail": str(exc),
            })
        image_worker.generate_scene_image.delay(project_id, generation.generation_id)
        queued.append({"scene_id": scene.scene_id,
                       "generation_id": generation.generation_id})
    return {"queued": queued, "skipped": skipped}


@app.post("/projects/{project_id}/scenes/{scene_id}/generate-image")
def generate_image(project_id: str, scene_id: str, session: SessionDep) -> dict:
    _, project = _load_project(session, project_id)
    if not any(s.scene_id == scene_id for s in project.scenes):
        raise HTTPException(404, f"scene {scene_id} not found")
    try:
        generation = image_worker.start_generation(session, project, scene_id)
    except image_worker.AttemptCapReached as exc:
        raise HTTPException(409, str(exc))
    except image_worker.ProviderNotConfigured as exc:
        # consumes nothing: no attempt, no Generation, no queue entry, no cost
        raise HTTPException(409, {
            "error_code": image_worker.ProviderNotConfigured.error_code,
            "detail": str(exc),
        })
    image_worker.generate_scene_image.delay(project_id, generation.generation_id)
    return {"generation_id": generation.generation_id, "status": generation.status}


@app.post("/projects/{project_id}/scenes/{scene_id}/generate-video")
def generate_video(project_id: str, scene_id: str, session: SessionDep) -> dict:
    """Stage 6: image-to-video for one scene. Requires a selected image."""
    _, project = _load_project(session, project_id)
    if not any(s.scene_id == scene_id for s in project.scenes):
        raise HTTPException(404, f"scene {scene_id} not found")
    try:
        generation = video_worker.start_video_generation(session, project, scene_id)
    except video_worker.AttemptCapReached as exc:
        raise HTTPException(409, str(exc))
    except image_worker.ProviderNotConfigured as exc:
        raise HTTPException(409, {
            "error_code": image_worker.ProviderNotConfigured.error_code,
            "detail": str(exc),
        })
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    video_worker.generate_scene_video.delay(
        project_id=project_id, generation_id=generation.generation_id)
    return {"generation_id": generation.generation_id, "status": generation.status}


class SelectImage(BaseModel):
    generation_id: str


@app.post("/projects/{project_id}/scenes/{scene_id}/select-image")
def select_image(project_id: str, scene_id: str, body: SelectImage,
                 session: SessionDep) -> dict:
    row, project = _load_project(session, project_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    gen = next((g for g in scene.generations
                if g.generation_id == body.generation_id), None)
    if gen is None:
        raise HTTPException(404, f"generation {body.generation_id} not found")
    if gen.status != "succeeded":
        raise HTTPException(422, f"generation status is {gen.status!r}, not succeeded")
    scene.selected_image = gen.generation_id
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"select image {body.generation_id} for {scene_id}")
    session.commit()
    return enriched(project)


@app.post("/projects/{project_id}/scenes/{scene_id}/select-video")
def select_video(project_id: str, scene_id: str, body: SelectImage,
                 session: SessionDep) -> dict:
    row, project = _load_project(session, project_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    gen = next((g for g in scene.generations
                if g.generation_id == body.generation_id and g.kind == "video"),
               None)
    if gen is None:
        raise HTTPException(404, f"video generation {body.generation_id} not found")
    if gen.status != "succeeded":
        raise HTTPException(422, f"generation status is {gen.status!r}, not succeeded")
    scene.selected_video = gen.generation_id
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"select video {body.generation_id} for {scene_id}")
    session.commit()
    return enriched(project)


@app.post("/projects/{project_id}/produce")
def produce(project_id: str, session: SessionDep) -> dict:
    """Run stages 6→9. Refuses unless every scene has a selected image."""
    import os

    _, project = _load_project(session, project_id)
    missing = [s.scene_id for s in project.scenes if not s.selected_image]
    if missing:
        raise HTTPException(422, {
            "error_code": "SCENES_NOT_APPROVED",
            "detail": f"every scene needs an approved image first; missing: {missing}",
        })
    for key in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        if not os.environ.get(key):
            raise HTTPException(409, {
                "error_code": "PROVIDER_NOT_CONFIGURED",
                "detail": f"{key} is not configured",
            })
    queued = []
    for scene_id in [s.scene_id for s in project.scenes]:
        # reload each iteration: workers may have mutated the row in between
        _, current = _load_project(session, project_id)
        scene = next(s for s in current.scenes if s.scene_id == scene_id)
        if scene.selected_video:
            continue
        if any(g.kind == "video" and g.status in ("queued", "running")
               for g in scene.generations):
            continue
        try:
            generation = video_worker.start_video_generation(
                session, current, scene_id)
        except video_worker.AttemptCapReached as exc:
            raise HTTPException(409, str(exc))
        video_worker.generate_scene_video.delay(
            project_id=project_id, generation_id=generation.generation_id)
        queued.append(generation.generation_id)
    produce_worker.produce_project.delay(project_id)
    return {"status": "producing", "video_generations_queued": queued}


@app.get("/projects/{project_id}/status")
def produce_status(project_id: str, session: SessionDep) -> dict:
    """Per-scene statuses + current pipeline stage for the progress UI."""
    _, project = _load_project(session, project_id)
    scenes = []
    any_video_activity = False
    for s in sorted(project.scenes, key=lambda x: x.order):
        active = [g for g in s.generations if g.status in ("queued", "running")]
        if any(g.kind == "video" for g in s.generations):
            any_video_activity = True
        scenes.append({
            "scene_id": s.scene_id, "order": s.order, "status": s.status,
            "image_attempts": s.generation_attempts,
            "video_attempts": video_worker.video_attempts(s),
            "active": [{"generation_id": g.generation_id, "kind": g.kind,
                        "status": g.status} for g in active],
        })
    if project.final_render:
        stage = "done"
    elif project.voiceover:
        stage = "rendering"
    elif any_video_activity:
        stage = "videos"
    elif all(s.selected_image for s in project.scenes):
        stage = "images_ready"
    elif any(s.generations for s in project.scenes):
        stage = "images"
    else:
        stage = "storyboard"
    return {"stage": stage, "scenes": scenes,
            "final_render_asset_id":
                project.final_render.asset_id if project.final_render else None}


@app.get("/projects/{project_id}/report")
def report(project_id: str, session: SessionDep) -> dict:
    """Per-stage cost + wall-clock — the Gate 3 unit-economics numbers."""
    _, project = _load_project(session, project_id)
    versions = (
        session.query(db.ProjectVersionRow)
        .filter_by(project_id=project_id)
        .order_by(db.ProjectVersionRow.created_at).all()
    )

    def first_ts(prefix: str):
        return next((v.created_at for v in versions
                     if v.reason.startswith(prefix)), None)

    def last_ts(prefix: str):
        return next((v.created_at for v in reversed(versions)
                     if v.reason.startswith(prefix)), None)

    def span(start_prefix: str, end_prefix: str):
        a, b = first_ts(start_prefix), last_ts(end_prefix)
        return round((b - a).total_seconds(), 1) if a and b else None

    image_gens = [g for s in project.scenes for g in s.generations
                  if g.kind == "image"]
    video_gens = [g for s in project.scenes for g in s.generations
                  if g.kind == "video"]
    return {
        "cost_cents": {**project.cost.model_dump(), "total": project.cost.total},
        "wall_clock_s": {
            "storyboard": span("stage1:", "stage4:"),
            "images": span("generate-image queued", "generation succeeded"),
            "videos": span("generate-video queued", "video generation succeeded"),
            "produce": span("produce:voiceover", "produce:rendered"),
        },
        "counts": {
            "scenes": len(project.scenes),
            "image_generations": len(image_gens),
            "image_regenerations": max(0, len(image_gens) - len(project.scenes)),
            "video_generations": len(video_gens),
            "qc_rejections": sum(1 for g in video_gens
                                 if g.status == "qc_rejected"),
        },
        "duration_s": sum(s.duration_s for s in project.scenes),
        "music_license": (project.music_license.model_dump(mode="json")
                          if project.music_license else None),
    }


@app.get("/projects/{project_id}/assets/{asset_id}")
def get_asset(project_id: str, asset_id: str, session: SessionDep,
              storage: StorageDep):
    """Stream an asset to the editor (browser can't read s3:// URIs)."""
    from fastapi.responses import Response as BytesResponse

    _, project = _load_project(session, project_id)
    assets = list(project.product.reference_images)
    for scene in project.scenes:
        assets += [g.asset for g in scene.generations if g.asset]
    if project.voiceover and project.voiceover.asset:
        assets.append(project.voiceover.asset)
    if project.music:
        assets.append(project.music)
    if project.final_render:
        assets.append(project.final_render)
    ref = next((a for a in assets if a.asset_id == asset_id), None)
    if ref is None:
        raise HTTPException(404, f"asset {asset_id} not found")
    data = storage.get_bytes(ref.uri)
    if ref.kind == "video" or ref.uri.endswith(".mp4"):
        mime = "video/mp4"
    elif ref.uri.endswith(".png") or ref.kind == "image":
        mime = "image/png"
    else:
        mime = "image/jpeg"
    return BytesResponse(content=data, media_type=mime)
