"""Celery app — redis broker/result backend."""
import os

from celery import Celery

from app import settings_store

# The worker reads provider keys from .env on startup (same as the API), so a
# worker restart picks up the current keys without needing them in the shell env.
settings_store.load_env()

celery_app = Celery(
    "ad_studio",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.autodiscover_tasks(["app.workers"])
