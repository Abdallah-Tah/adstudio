"""FastAPI entry point. Phase 0: /health only. Phase 1 adds project endpoints."""
from fastapi import FastAPI

app = FastAPI(title="AI Ad Studio", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
