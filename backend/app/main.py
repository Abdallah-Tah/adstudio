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
    doc = project.model_dump(mode="json")
    for scene, scene_doc in zip(project.scenes, doc["scenes"]):
        current = compile_image_prompt(
            scene, project.strategy.style_id, project.product
        ).prompt_hash
        for gen_doc in scene_doc["generations"]:
            gen_doc["stale"] = (
                gen_doc["kind"] == "image" and gen_doc["prompt_hash"] != current
            )
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

    from app.compiler.gpt_image import IMAGE_MODEL
    from app.compiler.kling_fal import VIDEO_MODEL
    from app.providers.openai_client import STAGE_MODEL

    def ok(key: str) -> bool:
        return bool(os.environ.get(key))

    return [
        {"id": "openai", "name": "OpenAI",
         "role": "Product analysis, strategy, storyboard + image generation",
         "models": [STAGE_MODEL, IMAGE_MODEL], "connected": ok("OPENAI_API_KEY")},
        {"id": "fal", "name": "fal.ai",
         "role": "Video generation — Kling v3 Standard, audio off (Phase 3)",
         "models": [VIDEO_MODEL], "connected": ok("FAL_KEY")},
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
    for scene in project.scenes:
        if scene.selected_image:
            skipped.append({"scene_id": scene.scene_id, "reason": "image selected"})
            continue
        try:
            generation = image_worker.start_generation(session, project, scene.scene_id)
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


@app.get("/projects/{project_id}/assets/{asset_id}")
def get_asset(project_id: str, asset_id: str, session: SessionDep,
              storage: StorageDep):
    """Stream an asset to the editor (browser can't read s3:// URIs)."""
    from fastapi.responses import Response as BytesResponse

    _, project = _load_project(session, project_id)
    assets = list(project.product.reference_images)
    for scene in project.scenes:
        assets += [g.asset for g in scene.generations if g.asset]
    ref = next((a for a in assets if a.asset_id == asset_id), None)
    if ref is None:
        raise HTTPException(404, f"asset {asset_id} not found")
    data = storage.get_bytes(ref.uri)
    mime = "image/png" if ref.uri.endswith(".png") or ref.kind == "image" else "image/jpeg"
    return BytesResponse(content=data, media_type=mime)
