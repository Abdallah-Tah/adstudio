"""Thin fal.ai queue REST client (verified against fal docs 2026-07-15).

Submit returns immediately with request_id + polling URLs — the Celery task
re-schedules itself between polls instead of blocking while fal renders.
"""
import base64
import os
from urllib.parse import urlencode

import httpx

QUEUE_BASE = "https://queue.fal.run"
TIMEOUT = 30.0


def _headers() -> dict:
    key = os.environ.get("FAL_KEY", "")
    if not key:
        raise RuntimeError("FAL_KEY is not configured")
    return {"Authorization": f"Key {key}"}


def data_uri(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def submit(
    model: str,
    payload: dict,
    *,
    webhook_url: str | None = None,
    timeout: float = TIMEOUT,
) -> dict:
    """POST to the queue. Returns {request_id, status_url, response_url}."""
    url = f"{QUEUE_BASE}/{model}"
    if webhook_url:
        url = f"{url}?{urlencode({'fal_webhook': webhook_url})}"
    r = httpx.post(url, json=payload, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    doc = r.json()
    return {"request_id": doc["request_id"],
            "status_url": doc["status_url"],
            "response_url": doc["response_url"],
            "cancel_url": doc.get("cancel_url"),
            "queue_position": doc.get("queue_position")}


def status(status_url: str, *, timeout: float = TIMEOUT) -> dict:
    """Return fal's status document (or raise on HTTP error)."""
    r = httpx.get(f"{status_url}?logs=1", headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def result(response_url: str, *, timeout: float = TIMEOUT) -> dict:
    r = httpx.get(response_url, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def cancel(cancel_url: str) -> dict:
    r = httpx.put(cancel_url, headers=_headers(), timeout=TIMEOUT)
    if r.status_code in (400, 404):
        try:
            return r.json()
        except ValueError:
            return {"status": "CANCEL_NOT_AVAILABLE"}
    r.raise_for_status()
    return r.json()


def download(url: str, *, timeout: float = 120.0) -> bytes:
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.content


def classify_error(exc: Exception) -> tuple[str, str, bool]:
    """Return (internal_code, sanitized_message, transient)."""
    status = getattr(exc, "response", None)
    status_code = getattr(status, "status_code", None)
    name = type(exc).__name__.lower()
    text = " ".join(str(exc).split())[:240] or type(exc).__name__

    if "timeout" in name:
        return "PROVIDER_SUBMISSION_TIMEOUT", "provider submission timeout", True
    if "connect" in name or "network" in name:
        return "PROVIDER_UNAVAILABLE", "temporary provider network failure", True
    if status_code in (401, 403):
        return "PROVIDER_AUTH_ERROR", "provider authentication or model access denied", False
    if status_code == 429:
        lowered = text.lower()
        if "balance" in lowered or "quota" in lowered or "insufficient" in lowered:
            return "PROVIDER_BALANCE_ERROR", "provider balance or quota is insufficient", False
        return "PROVIDER_RATE_LIMIT", "provider rate limit", True
    if status_code in (500, 502, 503, 504):
        return "PROVIDER_UNAVAILABLE", "provider temporarily unavailable", True
    if status_code == 400 or status_code == 422:
        lowered = text.lower()
        if "policy" in lowered or "safety" in lowered:
            return "PROVIDER_CONTENT_POLICY", "provider content policy rejection", False
        return "PROVIDER_INVALID_REQUEST", "provider rejected the request", False
    return "UNKNOWN_PROVIDER_ERROR", text, False
