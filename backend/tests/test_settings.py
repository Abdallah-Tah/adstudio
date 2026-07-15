"""Provider key settings: write-only storage into the env file."""
import os

from app import settings_store


def test_set_and_clear_provider_key(client, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=sqlite://\nFAL_KEY=\n")
    monkeypatch.setattr(settings_store, "ENV_FILE", env_file)

    r = client.post("/providers/fal/key", json={"api_key": "fal-test-key-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert "fal-test-key-123" not in str(body)  # never echoed back
    assert "FAL_KEY=fal-test-key-123" in env_file.read_text()
    assert os.environ["FAL_KEY"] == "fal-test-key-123"
    assert (env_file.stat().st_mode & 0o777) == 0o600

    # reflected in provider status
    fal = next(p for p in client.get("/providers").json() if p["id"] == "fal")
    assert fal["connected"] is True

    r = client.delete("/providers/fal/key")
    assert r.status_code == 200
    assert "FAL_KEY=\n" in env_file.read_text()
    assert "FAL_KEY" not in os.environ

    # unknown provider
    assert client.post("/providers/nope/key",
                       json={"api_key": "x" * 12}).status_code == 404
    # too-short key rejected by validation
    assert client.post("/providers/fal/key",
                       json={"api_key": "short"}).status_code == 422
