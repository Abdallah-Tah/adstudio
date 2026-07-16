"""DB-backed auth: real guard (no get_current_user override) + describe prefill."""
import pytest
import respx
from httpx import Response

from app import auth
from app.main import app, get_session, get_storage

from .conftest import FakeStorage, completion_payload, tiny_png


@pytest.fixture
def raw_client(sqlite_session, fake_storage, monkeypatch):
    """TestClient with the REAL auth guard active (session/storage stubbed)."""
    from fastapi.testclient import TestClient

    from app import db
    monkeypatch.setattr(db, "init_db", lambda *a, **k: None)
    app.dependency_overrides[get_session] = lambda: sqlite_session
    app.dependency_overrides[get_storage] = lambda: fake_storage
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_password_hashing_roundtrip():
    salt, h = auth.hash_password("correct horse battery")
    assert auth.verify_password("correct horse battery", salt, h)
    assert not auth.verify_password("wrong", salt, h)


def test_register_then_login_gate(raw_client):
    # protected before any auth
    assert raw_client.get("/projects").status_code == 401
    assert raw_client.get("/auth/status").json() == {
        "registered": False, "authenticated": False, "email": None}

    # first-run owner registration opens a session (cookie)
    r = raw_client.post("/auth/register",
                        json={"email": "Owner@Ad.co", "password": "s3cretpw!"})
    assert r.status_code == 200 and r.json()["email"] == "owner@ad.co"
    assert auth.COOKIE_NAME in r.cookies or auth.COOKIE_NAME in raw_client.cookies

    # now authenticated -> protected route reachable
    assert raw_client.get("/projects").status_code == 200
    status = raw_client.get("/auth/status").json()
    assert status == {"registered": True, "authenticated": True,
                      "email": "owner@ad.co"}

    # registration is closed once an owner exists
    assert raw_client.post("/auth/register",
                           json={"email": "b@b.co", "password": "another8x"}
                           ).status_code == 403

    # logout clears the session -> protected route blocked again
    assert raw_client.post("/auth/logout").status_code == 200
    assert raw_client.get("/projects").status_code == 401

    # wrong password rejected; right password restores access
    assert raw_client.post("/auth/login",
                           json={"email": "owner@ad.co", "password": "nope1234"}
                           ).status_code == 401
    assert raw_client.post("/auth/login",
                           json={"email": "owner@ad.co", "password": "s3cretpw!"}
                           ).status_code == 200
    assert raw_client.get("/projects").status_code == 200


def test_short_password_rejected(raw_client):
    r = raw_client.post("/auth/register",
                        json={"email": "x@y.co", "password": "short"})
    assert r.status_code == 422  # min_length=8


@respx.mock
def test_describe_prefill(client):
    """/describe returns an editable suggestion from up to 4 photos."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=Response(200, json=completion_payload(
            {"description": "A ceramic pour-over coffee set in matte white."})))
    r = client.post("/describe",
                    files=[("photos", ("a.png", tiny_png(), "image/png")),
                           ("photos", ("b.png", tiny_png(), "image/png"))])
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "A ceramic pour-over coffee set in matte white."
    assert body["cost_cents"] > 0


def test_describe_requires_a_photo(client):
    assert client.post("/describe", files=[]).status_code == 422
