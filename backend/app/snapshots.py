"""Snapshot-on-mutation helper.

Every mutation of a Project MUST go through one of these helpers — no
exceptions (Working agreement #5). During project creation the state is
partial (stages land one at a time), so snapshot_state() takes a plain dict;
snapshot_project() is the typed wrapper for a complete Project.
"""
import uuid

from sqlalchemy.orm import Session

from app.db import ProjectVersionRow
from app.schema import Project


def snapshot_state(
    session: Session, project_id: str, state: dict, actor: str, reason: str
) -> ProjectVersionRow:
    """Write an immutable snapshot (possibly partial state) to project_versions."""
    row = ProjectVersionRow(
        version_id=str(uuid.uuid4()),
        project_id=project_id,
        snapshot=state,
        actor=actor,
        reason=reason,
    )
    session.add(row)
    return row


def snapshot_project(
    session: Session, project: Project, actor: str, reason: str
) -> ProjectVersionRow:
    return snapshot_state(
        session, project.project_id, project.model_dump(mode="json"), actor, reason
    )
