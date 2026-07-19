#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
[ ! -f .env ] || . ./.env
set +a

# Image tasks update a shared project JSON ledger. Keep this queue serial so
# concurrent completions cannot overwrite each other's selected/QC state.
exec .venv/bin/celery -A app.workers.celery_app worker \
  --queues images \
  --concurrency 1 \
  --hostname images@%h \
  --loglevel INFO
