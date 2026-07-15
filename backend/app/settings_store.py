"""Provider API-key storage: upserts into the git-ignored backend/.env.

Write-only from the API's perspective — keys are NEVER returned to a client,
only a connected/not-connected boolean. Values are applied to the current
process immediately; the Celery worker picks them up on its next restart.
"""
import os
from pathlib import Path

ENV_FILE = Path(os.environ.get(
    "ADSTUDIO_ENV_FILE", Path(__file__).resolve().parent.parent / ".env"))

PROVIDER_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "fal": "FAL_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "music": "MUSIC_LIBRARY_KEY",
}


def _upsert(var: str, value: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    entry = f"{var}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{var}="):
            lines[i] = entry
            break
    else:
        lines.append(entry)
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def set_key(provider_id: str, value: str) -> None:
    var = PROVIDER_KEYS[provider_id]
    _upsert(var, value)
    os.environ[var] = value          # effective immediately in this process


def clear_key(provider_id: str) -> None:
    var = PROVIDER_KEYS[provider_id]
    _upsert(var, "")
    os.environ.pop(var, None)
