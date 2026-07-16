"""SQLAlchemy tables: projects (Project as JSONB), project_versions, generations."""
import os

from sqlalchemy import JSON, Text, TIMESTAMP, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# JSONB on Postgres; plain JSON on SQLite so tests can run in-memory.
JSONType = JSONB().with_variant(JSON(), "sqlite")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://adstudio:adstudio@localhost:5433/adstudio"
)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectVersionRow(Base):
    __tablename__ = "project_versions"

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class GenerationRow(Base):
    __tablename__ = "generations"

    generation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scene_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    salt: Mapped[str] = mapped_column(Text, nullable=False)          # hex
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # hex (scrypt)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class SessionRow(Base):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    expires_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


def make_engine(url: str = DATABASE_URL):
    return create_engine(url)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine())


def init_db(engine=None) -> None:
    Base.metadata.create_all(engine or make_engine())
