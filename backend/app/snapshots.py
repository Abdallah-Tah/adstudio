"""Snapshot-on-mutation helper.

Every mutation of a Project MUST go through snapshot_project() — no exceptions
(Working agreement #5).
"""
import uuid

from sqlalchemy.orm import Session

from app.db import ProjectVersionRow
from app.schema import Project


def snapshot_project(
    session: Session, project: Project, actor: str, reason: str
) -> ProjectVersionRow:
    """Write an immutable snapshot of the project state to project_versions."""
    row = ProjectVersionRow(
        version_id=str(uuid.uuid4()),
        project_id=project.project_id,
        snapshot=project.model_dump(mode="json"),
        actor=actor,
        reason=reason,
    )
    session.add(row)
    return row
