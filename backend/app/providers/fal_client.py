"""Thin fal.ai queue REST client (verified against fal docs 2026-07-15).

Submit returns immediately with request_id + polling URLs — the Celery task
re-schedules itself between polls instead of blocking while fal renders.
"""
import base64
import os

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


def submit(model: str, payload: dict) -> dict:
    """POST to the queue. Returns {request_id, status_url, response_url}."""
    r = httpx.post(f"{QUEUE_BASE}/{model}", json=payload,
                   headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    doc = r.json()
    return {"request_id": doc["request_id"],
            "status_url": doc["status_url"],
            "response_url": doc["response_url"]}


def status(status_url: str) -> str:
    """IN_QUEUE | IN_PROGRESS | COMPLETED (or raises on HTTP error)."""
    r = httpx.get(status_url, headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["status"]


def result(response_url: str) -> dict:
    r = httpx.get(response_url, headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def download(url: str) -> bytes:
    r = httpx.get(url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    return r.content
