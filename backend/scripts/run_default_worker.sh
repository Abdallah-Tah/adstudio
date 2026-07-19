#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
[ ! -f .env ] || . ./.env
set +a

exec .venv/bin/celery -A app.workers.celery_app worker \
  --queues celery \
  --concurrency 1 \
  --hostname default@%h \
  --loglevel INFO
