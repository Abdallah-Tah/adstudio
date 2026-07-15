#!/usr/bin/env bash
# Gate 2 identity benchmark runner. Requires: API on :8300, Celery worker
# running, real OPENAI_API_KEY in backend/.env, photos in benchmark/products/.
set -euo pipefail
cd "$(dirname "$0")/.."
API=http://localhost:8300
STYLES=(minimal_tech warm_lifestyle bold_energy studio_luxury ugc_handheld)

i=0
for dir in benchmark/products/*/; do
    slug=$(basename "$dir")
    desc=$(cat "$dir/description.txt")
    style=${STYLES[$((i % 5))]}
    i=$((i + 1))
    echo "=== $slug (style: $style) ==="

    args=()
    for f in "$dir"*.jpg "$dir"*.jpeg "$dir"*.png "$dir"*.webp; do
        [ -e "$f" ] && args+=(-F "photos=@$f")
    done
    pid=$(curl -sf "$API/projects" "${args[@]}" \
        -F "description=$desc" -F "style=$style" | python3.12 -c \
        'import json,sys; print(json.load(sys.stdin)["project_id"])')
    echo "project: $pid"

    curl -sf -X POST "$API/projects/$pid/storyboard/approve" > /dev/null
    curl -sf -X POST "$API/projects/$pid/generate-images" | python3.12 -m json.tool

    # wait for all generations to reach a terminal status
    for _ in $(seq 1 60); do
        busy=$(curl -sf "$API/projects/$pid" | python3.12 -c '
import json, sys
doc = json.load(sys.stdin)
gens = [g for s in doc["scenes"] for g in s["generations"]]
print(sum(g["status"] in ("queued", "running") for g in gens))')
        [ "$busy" = "0" ] && break
        sleep 5
    done

    (cd backend && set -a && . ./.env && set +a && \
        .venv/bin/python -m app.cli contact-sheet "$pid" \
        --out "../benchmark/contact_sheet_${slug}_${pid}.png")
done
echo "Done. Review sheets in benchmark/ against the rubric in README.md."
