"""FastAPI entry point. Phase 1: schema export, project creation, scene edits."""
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import db
from app.pipeline import create_project
from app.schema import Project
from app.snapshots import snapshot_project
from app.stages.brief import UserInputs
from app.stages.storyboard import StoryboardValidationError
from app.storage import Storage

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

SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]


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
) -> Project:
    if not photos:
        raise HTTPException(422, "at least one photo is required")
    user = UserInputs(
        audience=audience, offer=offer, cta=cta, tone=tone,
        style=style, target_duration_s=target_duration_s,
    )
    payloads = [(p.filename or "photo.jpg", await p.read()) for p in photos]
    try:
        return create_project(session, storage, payloads, description, user)
    except (StoryboardValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc))


def _load_project(session: Session, project_id: str) -> tuple[db.ProjectRow, Project]:
    row = session.get(db.ProjectRow, project_id)
    if row is None:
        raise HTTPException(404, f"project {project_id} not found")
    return row, Project.model_validate(row.data)


@app.get("/projects/{project_id}")
def get_project(project_id: str, session: SessionDep) -> Project:
    return _load_project(session, project_id)[1]


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
) -> Project:
    row, project = _load_project(session, project_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(422, "empty patch")
    idx = project.scenes.index(scene)
    project.scenes[idx] = scene.model_copy(update=updates)
    try:
        project = Project.model_validate(project.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(422, str(exc))
    row.data = project.model_dump(mode="json")
    snapshot_project(session, project, actor="user",
                     reason=f"scene edit {scene_id}: {sorted(updates)}")
    session.commit()
    return project
