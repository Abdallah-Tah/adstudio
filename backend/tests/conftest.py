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


@pytest.fixture
def fake_storage():
    return FakeStorage()
