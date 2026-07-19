import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")

from app import db  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def completion_payload(content: dict, prompt_tokens: int = 1200,
                       completion_tokens: int = 250) -> dict:
    """A recorded-style chat.completions response wrapping a structured output."""
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 1752451200,
        "model": "gpt-5.4-mini-2026-03-17",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(content)},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@pytest.fixture
def sqlite_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.init_db(engine)
    session = db.make_session_factory(engine)()
    yield session
    session.close()


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket = "test-bucket"

    def ensure_bucket(self) -> None:
        pass

    def put_bytes(self, data: bytes, key: str, content_type: str) -> str:
        self.objects[key] = data
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, uri: str) -> bytes:
        return self.objects[uri.removeprefix(f"s3://{self.bucket}/")]


@pytest.fixture
def fake_storage():
    return FakeStorage()


def tiny_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def eager_worker(sqlite_session, fake_storage, monkeypatch):
    """Run the Celery task body synchronously against the test session."""
    from app.workers import images as image_worker

    def fake_delay(project_id, generation_id):
        return image_worker.run_generation(
            sqlite_session, fake_storage, project_id, generation_id)
    monkeypatch.setattr(image_worker.generate_scene_image, "delay", fake_delay)


@pytest.fixture
def good_provider(monkeypatch):
    from app.providers import openai_images
    monkeypatch.setattr(openai_images, "generate_image",
                        lambda *a, **k: (tiny_png(), 11))


@pytest.fixture
def bad_provider(monkeypatch):
    from app.providers import openai_images

    def boom(*a, **k):
        raise RuntimeError("deliberately bad prompt")
    monkeypatch.setattr(openai_images, "generate_image", boom)


@pytest.fixture(autouse=True)
def passing_image_identity_qc(monkeypatch):
    from app.schema import ProductIdentityQC
    from app.stages import image_identity_qc

    verdict = ProductIdentityQC(
        identity_score=0.95,
        silhouette_match=True,
        proportions_match=True,
        colors_match=True,
        materials_match=True,
        notes="identity preserved",
    )
    monkeypatch.setattr(image_identity_qc, "run_qc", lambda *a, **k: (verdict, 0))


@pytest.fixture
def client(sqlite_session, fake_storage, monkeypatch):
    """TestClient wired to sqlite + fake storage; rembg stubbed out."""
    from fastapi.testclient import TestClient

    from app import pipeline
    from app.main import app, get_current_user, get_session, get_storage

    monkeypatch.setattr(db, "init_db", lambda *a, **k: None)  # lifespan no-op
    monkeypatch.setattr(pipeline.segmentation, "segment",
                        lambda raw: (tiny_png(), None))
    app.dependency_overrides[get_session] = lambda: sqlite_session
    app.dependency_overrides[get_storage] = lambda: fake_storage
    app.dependency_overrides[get_current_user] = lambda: "test@adstudio.local"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def create_test_project(client) -> dict:
    """POST /projects with the recorded stage fixtures mocked in (respx must be active)."""
    import respx
    from httpx import Response

    respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=[
        Response(200, json=completion_payload(load_fixture(n)))
        for n in ("analysis", "brief", "strategy", "storyboard")
    ])
    resp = client.post(
        "/projects",
        files=[("photos", ("cup.png", tiny_png(), "image/png"))],
        data={"description": "A ceramic pour-over set.",
              "offer": "20% off launch week", "cta": "Shop now"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
