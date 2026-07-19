"""FastAPI entry point. Phase 1: schema export, project creation, scene edits."""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import auth, db, generation_config, production_readiness, settings_store
from app.compiler.gpt_image import compile_image_prompt

# Load provider keys from .env into the environment before anything reads them.
settings_store.load_env()
from app.pipeline import create_project
from app.schema import Project, QCOverride, StoryboardApproval
from app.snapshots import snapshot_project
from app.stages import scene_consistency
from app.stages.brief import UserInputs
from app.stages.storyboard import StoryboardValidationError
from app.storage import Storage
from app.workers import images as image_worker
from app.workers import produce as produce_worker
from app.workers import videos as video_worker

log = logging.getLogger("adstudio.api")
VIDEO_QC_OVERRIDE_ACK = "I understand this video may not accurately match the product."

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
    try:
        video_worker.recover_active_video_generations.delay()
    except Exception:
        pass
    yield


# Paths reachable without a session (login flow + liveness).
_OPEN_PATHS = {
    "/health", "/schema", "/openapi.json", "/docs", "/redoc",
    "/api/webhooks/fal", "/webhooks/fal",
}
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def get_current_user(request: Request, session: "Session" = Depends(get_session)):
    """Global auth guard. Returns the current user's email, or None for open
    paths. Raises 401 on any protected path without a valid session."""
    path = request.url.path
    if request.method == "OPTIONS" or path in _OPEN_PATHS or path.startswith("/auth/"):
        return None
    user = auth.user_for_token(session, request.cookies.get(auth.COOKIE_NAME))
    if user is None:
        raise HTTPException(401, "authentication required")
    return user.email


app = FastAPI(title="AI Ad Studio", version="0.1.0", lifespan=lifespan,
              dependencies=[Depends(get_current_user)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # editor v0, single-user localhost
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]


# ------------------------------------------------------------------- auth ---

class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=COOKIE_SECURE, path="/",
        max_age=int(auth.SESSION_TTL.total_seconds()))


@app.get("/auth/status")
def auth_status(request: Request, session: SessionDep) -> dict:
    """Frontend bootstrap: is an owner registered, and am I logged in?"""
    user = auth.user_for_token(session, request.cookies.get(auth.COOKIE_NAME))
    return {"registered": auth.user_count(session) > 0,
            "authenticated": user is not None,
            "email": user.email if user else None}


@app.post("/auth/register")
def auth_register(body: Credentials, session: SessionDep,
                  response: Response) -> dict:
    """First-run owner signup only — closed once an account exists."""
    if auth.user_count(session) > 0:
        raise HTTPException(403, "registration is closed; an owner already exists")
    user = auth.create_user(session, body.email, body.password)
    _set_session_cookie(response, auth.create_session(session, user.user_id))
    return {"email": user.email}


@app.post("/auth/login")
def auth_login(body: Credentials, session: SessionDep, response: Response) -> dict:
    user = auth.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, "invalid email or password")
    _set_session_cookie(response, auth.create_session(session, user.user_id))
    return {"email": user.email}


@app.post("/auth/logout")
def auth_logout(request: Request, session: SessionDep, response: Response) -> dict:
    auth.revoke(session, request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


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


@app.post("/describe")
async def describe(photos: list[UploadFile]) -> dict:
    """1-4 product photos -> a suggested, editable description (create-form prefill)."""
    from app.stages import describe as describe_stage

    if not photos:
        raise HTTPException(422, "at least one photo is required")
    payloads = [(await p.read(), p.content_type or "image/jpeg")
                for p in photos[:describe_stage.MAX_PHOTOS]]
    try:
        text, cost = await run_in_threadpool(describe_stage.run, payloads)
    except Exception as exc:
        raise HTTPException(502, f"could not describe photos: {exc}")
    return {"description": text, "cost_cents": cost}


@app.post("/uploads/precheck")
async def uploads_precheck(photos: list[UploadFile]) -> dict:
    """Customer-facing photo quality gate, run before a project exists.

    Tells the customer per photo whether it is good enough to build a correct
    ad from (good / usable / replace + friendly tips). Consumes nothing
    durable; the optional AI screen is one metered vision call."""
    from app.stages import upload_precheck

    if not photos:
        raise HTTPException(422, "at least one photo is required")
    payloads = [(p.filename or f"photo-{i + 1}.jpg", await p.read(),
                 p.content_type or "image/jpeg")
                for i, p in enumerate(photos[:8])]
    result = await run_in_threadpool(upload_precheck.run, payloads)
    return result.model_dump(mode="json")


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
    mode: Annotated[Literal["manual", "auto"], Form()] = "manual",
) -> dict:
    if not photos:
        raise HTTPException(422, "at least one photo is required")
    user = UserInputs(
        audience=audience, offer=offer, cta=cta, tone=tone,
        style=style, target_duration_s=target_duration_s,
    )
    payloads = [(p.filename or "photo.jpg", await p.read()) for p in photos]
    try:
        # create_project is ~40-50s of blocking CPU (rembg) + network (4 LLM
        # stages) for a multi-photo batch. Run it off the event loop so the
        # single-worker server stays responsive and the connection isn't reset.
        project = await run_in_threadpool(
            lambda: create_project(session, storage, payloads, description, user))
        if mode == "auto":
            await run_in_threadpool(
                lambda: _start_autopilot(session, project.project_id))
            _, project = _load_project(session, project.project_id)
        return enriched(project)
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
    if any(g["status"] in (
        "queued", "submitting", "provider_queued", "provider_processing",
        "downloading", "uploading", "qc_running", "running", "retrying",
    )
           for s in scenes for g in s.get("generations", [])):
        return "running"
    if any(s.get("selected_image") for s in scenes):
        return "image_ready"
    return "storyboard"


def _generation_in_project(project: Project, generation_id: str):
    for scene in project.scenes:
        for gen in scene.generations:
            if gen.generation_id == generation_id:
                return scene, gen
    if project.voiceover and project.voiceover.generation_id == generation_id:
        return None, project.voiceover
    return None, None


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
    from app.workers import reaper
    project = _load_project(session, project_id)[1]
    # Watchdog: converge any jobs orphaned by a dead/hung worker to "failed"
    # before returning, so the polling UI never sees "running" forever.
    if reaper.reap_stuck(session, project):
        project = _load_project(session, project_id)[1]
    return enriched(project)


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


def _start_autopilot(session: Session, project_id: str) -> Project:
    """Approve the storyboard (auto mode is explicit consent for the build)
    and hand the project to the auto-pilot worker."""
    from datetime import datetime, timezone

    from app.workers import autopilot

    row, project = _load_project(session, project_id)
    if project.storyboard_approval.status != "approved":
        project.storyboard_approval = StoryboardApproval(
            status="approved",
            approved_at=datetime.now(timezone.utc).isoformat(),
            approved_by="autopilot",
        )
        version = snapshot_project(session, project, actor="autopilot",
                                   reason="storyboard approved (auto mode)")
        project.storyboard_approval.approved_version_id = version.version_id
    project.automation.mode = "auto"
    project.automation.status = "generating_images"
    project.automation.detail = "Generating and quality-checking a still for every scene."
    project.automation.updated_at = datetime.now(timezone.utc).isoformat()
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="autopilot", reason="autopilot started")
    session.commit()
    autopilot.run_autopilot.delay(project_id)
    return project


class AutopilotAction(BaseModel):
    action: Literal["start", "stop"]


@app.post("/projects/{project_id}/autopilot")
def autopilot_control(project_id: str, body: AutopilotAction,
                      session: SessionDep) -> dict:
    """Start/resume or pause the hands-free build. Stopping returns the
    project to normal manual editing; nothing is deleted."""
    from datetime import datetime, timezone

    row, project = _load_project(session, project_id)
    if body.action == "start":
        project = _start_autopilot(session, project_id)
        return enriched(project)
    project.automation.mode = "manual"
    project.automation.status = "idle"
    project.automation.detail = "Paused — you are in manual control."
    project.automation.updated_at = datetime.now(timezone.utc).isoformat()
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user", reason="autopilot paused")
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
            started = image_worker.start_generation(session, current, scene.scene_id)
        except image_worker.AttemptCapReached as exc:
            skipped.append({"scene_id": scene.scene_id, "reason": str(exc)})
            continue
        except image_worker.ProviderNotConfigured as exc:
            raise HTTPException(409, {
                "error_code": image_worker.ProviderNotConfigured.error_code,
                "detail": str(exc),
            })
        if started.created:
            image_worker.generate_scene_image.delay(
                project_id, started.generation.generation_id)
            queued.append({"scene_id": scene.scene_id,
                           "generation_id": started.generation.generation_id})
        else:
            skipped.append({"scene_id": scene.scene_id,
                            "generation_id": started.generation.generation_id,
                            "reason": "generation already active"})
    return {"queued": queued, "skipped": skipped}


@app.post("/projects/{project_id}/scenes/{scene_id}/generate-image")
def generate_image(project_id: str, scene_id: str, session: SessionDep) -> dict:
    _, project = _load_project(session, project_id)
    if not any(s.scene_id == scene_id for s in project.scenes):
        raise HTTPException(404, f"scene {scene_id} not found")
    try:
        started = image_worker.start_generation(session, project, scene_id)
    except image_worker.AttemptCapReached as exc:
        raise HTTPException(409, str(exc))
    except image_worker.ProviderNotConfigured as exc:
        # consumes nothing: no attempt, no Generation, no queue entry, no cost
        raise HTTPException(409, {
            "error_code": image_worker.ProviderNotConfigured.error_code,
            "detail": str(exc),
        })
    if started.created:
        image_worker.generate_scene_image.delay(
            project_id, started.generation.generation_id)
    return {"generation_id": started.generation.generation_id,
            "status": started.generation.status,
            "duplicate": not started.created}


@app.post("/projects/{project_id}/generations/{generation_id}/cancel")
def cancel_generation(project_id: str, generation_id: str,
                      session: SessionDep) -> dict:
    from datetime import datetime, timezone

    from app.providers import fal_client

    row, project = _load_project(session, project_id)
    _, gen = _generation_in_project(project, generation_id)
    if gen is None:
        raise HTTPException(404, f"generation {generation_id} not found")
    if gen.status not in (
        "queued", "submitting", "provider_queued", "provider_processing",
        "downloading", "uploading", "qc_running", "running", "retrying",
    ):
        raise HTTPException(409, f"generation is already {gen.status}")
    if gen.kind == "video" and gen.provider_cancel_url:
        try:
            result = fal_client.cancel(gen.provider_cancel_url)
            gen.provider_status = result.get("status", gen.provider_status)
        except Exception:
            gen.provider_status = gen.provider_status or "CANCEL_REQUEST_FAILED"
    gen.status = "cancelled"
    gen.error_code = "CANCELLED"
    gen.error_message = "cancelled by user"
    gen.qc_notes = "cancelled by user"
    gen.finished_at = datetime.now(timezone.utc).isoformat()
    row.data = project.model_dump(mode="json")
    gen_row = session.get(db.GenerationRow, generation_id)
    if gen_row is not None:
        gen_row.data = gen.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"cancel generation {generation_id}")
    session.commit()
    return enriched(project)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/generations/{generation_id}/cancel")
@app.post("/projects/{project_id}/scenes/{scene_id}/generations/{generation_id}/cancel")
def cancel_scene_generation(project_id: str, scene_id: str, generation_id: str,
                            session: SessionDep) -> dict:
    return cancel_generation(project_id, generation_id, session)


@app.post("/projects/{project_id}/clear-stale-generations")
def clear_stale_generations(project_id: str, session: SessionDep) -> dict:
    from app.workers import reaper

    _, project = _load_project(session, project_id)
    changed = reaper.reap_stuck(session, project)
    if changed:
        _, project = _load_project(session, project_id)
    return {"changed": changed, "project": enriched(project)}


@app.post("/projects/{project_id}/scenes/{scene_id}/generate-video")
def generate_video(project_id: str, scene_id: str, session: SessionDep) -> dict:
    """Stage 6: image-to-video for one scene. Requires a selected image."""
    _, project = _load_project(session, project_id)
    if not any(s.scene_id == scene_id for s in project.scenes):
        raise HTTPException(404, f"scene {scene_id} not found")
    try:
        started = video_worker.start_video_generation(session, project, scene_id)
    except video_worker.AttemptCapReached as exc:
        raise HTTPException(409, {
            "error_code": "VIDEO_ATTEMPTS_EXHAUSTED",
            "scene_id": scene_id,
            "detail": str(exc),
        })
    except image_worker.ProviderNotConfigured as exc:
        raise HTTPException(409, {
            "error_code": image_worker.ProviderNotConfigured.error_code,
            "detail": str(exc),
        })
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if started.created:
        video_worker.generate_scene_video.delay(
            project_id=project_id,
            generation_id=started.generation.generation_id)
    return {"generation_id": started.generation.generation_id,
            "status": started.generation.status,
            "duplicate": not started.created}


class SelectImage(BaseModel):
    generation_id: str


class OverrideQC(BaseModel):
    acknowledgement: str = Field(min_length=10, max_length=300)
    reason: str = Field(default="", max_length=500)


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


@app.post("/projects/{project_id}/scenes/{scene_id}/videos/{generation_id}/override-qc")
def override_video_qc(project_id: str, scene_id: str, generation_id: str,
                      body: OverrideQC, session: SessionDep,
                      actor: str = Depends(get_current_user)) -> dict:
    row, project = _load_project(session, project_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    gen = next((g for g in scene.generations
                if g.generation_id == generation_id and g.kind == "video"),
               None)
    if gen is None:
        raise HTTPException(404, f"video generation {generation_id} not found")
    if gen.status != "qc_rejected":
        raise HTTPException(422, f"generation status is {gen.status!r}, not qc_rejected")
    if gen.asset is None:
        raise HTTPException(422, "cannot override QC for a video without an asset")
    if body.acknowledgement.strip() != VIDEO_QC_OVERRIDE_ACK:
        raise HTTPException(422, {
            "error_code": "QC_OVERRIDE_ACK_REQUIRED",
            "required_acknowledgement": VIDEO_QC_OVERRIDE_ACK,
        })

    gen.qc_override = QCOverride(
        overridden_by=actor or "user",
        overridden_at=datetime.now(timezone.utc).isoformat(),
        reason=body.reason.strip(),
        acknowledgement=body.acknowledgement.strip(),
    )
    gen.status = "succeeded"
    gen.error_code = None
    gen.error_message = None
    scene.selected_video = gen.generation_id
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor=actor or "user",
                     reason=f"override video QC {generation_id} for {scene_id}")
    session.commit()
    log.info("video QC overridden project=%s scene=%s generation=%s actor=%s",
             project_id, scene_id, generation_id, actor or "user")
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


def _run_scene_consistency(session: Session, storage: Storage,
                           project_id: str) -> Project:
    """Run the cross-scene identity check, store the report, meter the spend."""
    row, project = _load_project(session, project_id)
    try:
        report, cost = scene_consistency.run_check(project, storage)
    except ValueError as exc:
        raise HTTPException(422, {
            "error_code": "IDENTITY_CONSISTENCY_INPUT_MISSING",
            "message": str(exc),
        })
    project.scene_consistency = report
    project.cost.qc += cost
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"identity-consistency check "
                            f"({'consistent' if report.consistent else 'inconsistent'})")
    session.commit()
    log.info("scene consistency project=%s consistent=%s cost_cents=%d",
             project_id, report.consistent, cost)
    return project


@app.post("/projects/{project_id}/identity-consistency")
def identity_consistency(project_id: str, session: SessionDep,
                         storage: StorageDep) -> dict:
    """Re-judge all selected scene images against the primary product reference."""
    project = _run_scene_consistency(session, storage, project_id)
    return project.scene_consistency.model_dump(mode="json")


@app.get("/api/projects/{project_id}/production-readiness")
@app.get("/projects/{project_id}/production-readiness")
def production_readiness_endpoint(project_id: str, session: SessionDep) -> dict:
    _, project = _load_project(session, project_id)
    readiness = production_readiness.validate(project)
    log.info(
        "production preflight project=%s ready=%s blockers=%d estimated_video_cost_cents=%d",
        project_id,
        readiness.ready,
        len(readiness.blocking_reasons),
        readiness.estimated_video_cost_cents,
    )
    return readiness.model_dump(mode="json")


@app.post("/projects/{project_id}/produce")
def produce(project_id: str, session: SessionDep, storage: StorageDep) -> dict:
    """Run stages 6→9. Backend readiness is the source of truth."""
    row, project = _load_project(session, project_id)
    active_job = production_readiness.active_job(project)
    if active_job:
        return {"status": "producing", "production_job": active_job.model_dump(mode="json")}
    if (
        generation_config.SCENE_CONSISTENCY_QC_ENABLED
        and not scene_consistency.is_current(project)
        and all(s.selected_image for s in project.scenes)
        and os.environ.get("OPENAI_API_KEY")
    ):
        # cross-scene identity gate: judged BEFORE any video spend
        project = _run_scene_consistency(session, storage, project_id)
        row, project = _load_project(session, project_id)
    readiness = production_readiness.validate(project)
    if not readiness.ready:
        log.info("production blocked project=%s blockers=%s",
                 project_id, [r.code for r in readiness.blocking_reasons])
        raise HTTPException(409, {
            "error_code": "PROJECT_NOT_READY",
            **readiness.model_dump(mode="json"),
        })
    job = production_readiness.new_job(project)
    project.production_job = job
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"production queued {job.production_job_id}")
    session.commit()
    log.info("production queued project=%s production_job_id=%s estimated_video_cost_cents=%d",
             project_id, job.production_job_id, readiness.estimated_video_cost_cents)
    queued = []
    for scene_id in [s.scene_id for s in project.scenes]:
        # reload each iteration: workers may have mutated the row in between
        _, current = _load_project(session, project_id)
        scene = next(s for s in current.scenes if s.scene_id == scene_id)
        if scene.selected_video:
            continue
        if any(g.kind == "video" and g.status in (
            "queued", "submitting", "provider_queued", "provider_processing",
            "downloading", "uploading", "qc_running", "running", "retrying",
        )
               for g in scene.generations):
            continue
        try:
            started = video_worker.start_video_generation(
                session, current, scene_id)
        except video_worker.AttemptCapReached as exc:
            raise HTTPException(409, str(exc))
        if started.created:
            video_worker.generate_scene_video.delay(
                project_id=project_id,
                generation_id=started.generation.generation_id)
        queued.append(started.generation.generation_id)
    produce_worker.produce_project.delay(project_id)
    return {
        "status": "producing",
        "production_job": job.model_dump(mode="json"),
        "video_generations_queued": queued,
    }


@app.get("/projects/{project_id}/status")
def produce_status(project_id: str, session: SessionDep) -> dict:
    """Per-scene statuses + current pipeline stage for the progress UI."""
    from app.workers import reaper
    _, project = _load_project(session, project_id)
    if reaper.reap_stuck(session, project):
        _, project = _load_project(session, project_id)
    scenes = []
    any_video_activity = False
    for s in sorted(project.scenes, key=lambda x: x.order):
        active = [g for g in s.generations if g.status in (
            "queued", "submitting", "provider_queued", "provider_processing",
            "downloading", "uploading", "qc_running", "running", "retrying",
        )]
        if any(g.kind == "video" for g in s.generations):
            any_video_activity = True
        scenes.append({
            "scene_id": s.scene_id, "order": s.order, "status": s.status,
            "image_attempts": s.generation_attempts,
            "video_attempts": video_worker.video_attempts(s),
            "active": [{"generation_id": g.generation_id, "kind": g.kind,
                        "status": g.status,
                        "queued_at": g.queued_at or g.created_at,
                        "started_at": g.started_at,
                        "provider_job_id": g.provider_job_id,
                        "provider_status": g.provider_status,
                        "provider_progress": g.provider_progress,
                        "last_heartbeat_at": g.last_heartbeat_at,
                        "attempt_number": g.attempt_number,
                        "error_code": g.error_code} for g in active],
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
    return {
        "stage": stage,
        "scenes": scenes,
        "production_job": (
            project.production_job.model_dump(mode="json")
            if project.production_job else None
        ),
        "final_render_asset_id":
            project.final_render.asset_id if project.final_render else None,
    }


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


@app.get("/debug/jobs")
def debug_jobs(session: SessionDep) -> dict:
    """Developer diagnostics: every generation's live state + worker/queue health.

    Read-only. Surfaces exactly where a job is stuck: queued (no worker),
    running (in-flight or hung), or terminal. No prompts/secrets are exposed.
    """
    import os
    from datetime import datetime, timezone

    from app.workers import reaper

    def elapsed(ts: str | None) -> float | None:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return round((datetime.now(timezone.utc) - dt).total_seconds(), 1)
        except ValueError:
            return None

    STEP = {
        "queued": "waiting for worker",
        "submitting": "submitting to provider",
        "provider_queued": "queued at provider",
        "provider_processing": "provider processing",
        "downloading": "downloading video",
        "uploading": "uploading asset",
        "qc_running": "running QC",
        "running": "provider call in-flight",
        "retrying": "waiting to retry",
        "succeeded": "done",
        "failed": "failed",
        "timed_out": "timed out",
        "cancelled": "cancelled",
        "qc_rejected": "QC rejected",
    }

    # attempt counts live on the scene, keyed for lookup
    attempts: dict[str, int] = {}
    for row in session.query(db.ProjectRow).all():
        for s in row.data.get("scenes", []):
            for g in s.get("generations", []):
                attempts[g["generation_id"]] = s.get("generation_attempts", 0)

    jobs = []
    for r in session.query(db.GenerationRow).order_by(db.GenerationRow.generation_id).all():
        d = r.data
        active = d.get("status") in (
            "queued", "submitting", "provider_queued", "provider_processing",
            "downloading", "uploading", "qc_running", "running", "retrying",
        )
        jobs.append({
            "generation_id": r.generation_id, "project_id": r.project_id,
            "scene_id": r.scene_id, "kind": d.get("kind"), "status": d.get("status"),
            "provider": d.get("provider"), "model": d.get("model"),
            "attempts": attempts.get(r.generation_id),
            "created_at": d.get("created_at"),
            "queued_at": d.get("queued_at") or d.get("created_at"),
            "started_at": d.get("started_at"),
            "provider_called_at": d.get("provider_called_at"),
            "provider_completed_at": d.get("provider_completed_at"),
            "provider_job_id": d.get("provider_job_id"),
            "provider_status": d.get("provider_status"),
            "provider_progress": d.get("provider_progress"),
            "provider_submitted_at": d.get("provider_submitted_at"),
            "last_provider_check_at": d.get("last_provider_check_at"),
            "next_provider_check_at": d.get("next_provider_check_at"),
            "last_heartbeat_at": d.get("last_heartbeat_at"),
            "asset_uploaded_at": d.get("asset_uploaded_at"),
            "finished_at": d.get("finished_at"),
            "elapsed_s": elapsed(d.get("started_at") or d.get("created_at")) if active else None,
            "current_step": STEP.get(d.get("status"), d.get("status")),
            "attempt_number": d.get("attempt_number", 1),
            "queue_wait_ms": d.get("queue_wait_ms"),
            "provider_latency_ms": d.get("provider_latency_ms"),
            "download_latency_ms": d.get("download_latency_ms"),
            "upload_latency_ms": d.get("upload_latency_ms"),
            "qc_latency_ms": d.get("qc_latency_ms"),
            "total_latency_ms": d.get("total_latency_ms"),
            "error_code": d.get("error_code"),
            "sanitized_message": d.get("error_message") or d.get("qc_notes"),
            "cost_cents": d.get("cost_cents", 0),
            "note": d.get("qc_notes"),
        })

    # worker + queue health
    from app.workers.celery_app import celery_app
    workers: dict = {"registered": None, "active": None, "reserved": None, "alive": False}
    try:
        insp = celery_app.control.inspect(timeout=1.0)
        reg = insp.registered() or {}
        workers = {
            "alive": bool(reg),
            "registered": reg,
            "active": insp.active() or {},
            "reserved": insp.reserved() or {},
        }
    except Exception as exc:  # pragma: no cover - inspect best-effort
        workers["error"] = str(exc)

    queue_len = None
    try:
        import redis
        rc = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        queue_len = {
            "default": rc.llen("celery"),
            "images": rc.llen("images"),
        }
    except Exception:
        pass

    return {
        "jobs": jobs,
        "counts": {
            "queued": sum(1 for j in jobs if j["status"] == "queued"),
            "submitting": sum(1 for j in jobs if j["status"] == "submitting"),
            "provider_queued": sum(1 for j in jobs if j["status"] == "provider_queued"),
            "provider_processing": sum(1 for j in jobs if j["status"] == "provider_processing"),
            "downloading": sum(1 for j in jobs if j["status"] == "downloading"),
            "uploading": sum(1 for j in jobs if j["status"] == "uploading"),
            "qc_running": sum(1 for j in jobs if j["status"] == "qc_running"),
            "running": sum(1 for j in jobs if j["status"] == "running"),
            "retrying": sum(1 for j in jobs if j["status"] == "retrying"),
            "failed": sum(1 for j in jobs if j["status"] == "failed"),
            "timed_out": sum(1 for j in jobs if j["status"] == "timed_out"),
            "cancelled": sum(1 for j in jobs if j["status"] == "cancelled"),
            "succeeded": sum(1 for j in jobs if j["status"] == "succeeded"),
        },
        "worker": workers,
        "queue_length": queue_len,
        "watchdog": {"run_timeouts_s": reaper.RUN_TIMEOUTS, "queue_timeout_s": reaper.QUEUE_TIMEOUT},
    }


@app.post("/api/webhooks/fal")
@app.post("/webhooks/fal")
async def fal_webhook(request: Request, session: SessionDep) -> dict:
    """fal queue completion callback.

    The handler only persists provider state and enqueues finalization so fal
    gets a quick 200. Duplicate callbacks are idempotent.
    """
    import json

    from app import generation_config

    raw = await request.body()
    if generation_config.FAL_WEBHOOK_VERIFY:
        raise HTTPException(501, {
            "error_code": "WEBHOOK_VERIFICATION_NOT_CONFIGURED",
            "detail": "fal ED25519 verification requires a crypto dependency",
        })
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON webhook payload")
    return video_worker.handle_fal_webhook(session, payload)


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
