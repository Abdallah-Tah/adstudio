# Vendored code

## freecut

- **Source:** local repo `~/Developer/freecut` (the freecut editing toolkit)
- **Pinned commit:** `f1d43341204fffe3b8352e8dfe6f507f87bd5637`
- **Vendored on:** 2026-07-15
- **Files taken:**
  - `helpers/render.py` → `vendor/freecut/render.py` — EDL render pipeline:
    per-segment extract with 30ms audio fades, lossless `-c copy` concat,
    overlay PTS-shift, `subtitles` filter applied LAST, platform-safe
    `MarginV=90` caption style for 1080×1920.
  - `helpers/grade.py` → `vendor/freecut/grade.py` — ffmpeg grade presets
    (`warm_cinematic`, `neutral_punch`) used per-segment during extraction.

These are reference implementations, never a live dependency — the ad render
worker (`app/stages/render.py`) follows their hard rules (fades at every cut,
concat-then-overlay ordering, captions last, output-timeline subtitle offsets)
and reuses the grade presets. Update only by re-copying at a new pinned commit
and recording it here.
