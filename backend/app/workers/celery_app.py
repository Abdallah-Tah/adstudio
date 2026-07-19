"""Celery app — redis broker/result backend."""
import os

from celery import Celery

from app import settings_store

# The worker reads provider keys from .env on startup (same as the API), so a
# worker restart picks up the current keys without needing them in the shell env.
settings_store.load_env()

REDIS = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# IMPORTANT: task modules must be imported for their @celery_app.task decorators
# to register. The old autodiscover_tasks(["app.workers"]) looked for a
# nonexistent `app.workers.tasks` module and therefore registered NOTHING — the
# worker's [tasks] list was empty, so every enqueued job sat in Redis forever.
# `include` imports the real task modules on worker startup.
celery_app = Celery(
    "ad_studio",
    broker=REDIS,
    backend=REDIS,
    include=[
        "app.workers.images",
        "app.workers.videos",
        "app.workers.produce",
    ],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

# Safety net: no task runs forever. Image generation is a single short call;
# video/produce poll fal.ai but each execution (submit or one poll) is quick and
# reschedules via self.retry, so a per-execution limit is safe for all of them.
# soft_time_limit raises SoftTimeLimitExceeded *inside* the task (caught → the
# job is marked failed); time_limit is the hard SIGKILL backstop.
celery_app.conf.task_soft_time_limit = 120
celery_app.conf.task_time_limit = 150
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.task_routes = {
    "app.workers.images.generate_scene_image": {"queue": "images"},
}

# The worker imports the `include` modules during boot (import_default_modules),
# which registers their @task decorators — that's what fixes the empty [tasks]
# list. The API producer registers them separately by importing app.workers.*
# directly. /debug/jobs reads the worker's registry over Redis via inspect(),
# not this process's local registry, so no import is forced here (a top-level
# import would hit the images<->celery_app<->videos cycle).
