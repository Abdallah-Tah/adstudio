# AI Ad Studio v3 — Claude Code project instructions

Pipeline: product photos → platform-ready TikTok ad (9:16 MP4), with human review gates between expensive stages. The complete, authoritative plan is **BUILD_PLAN.md** — follow it phase by phase. This file summarizes the working agreement and tracks phase status.

## Working agreement (applies to every phase)

1. **STOP gates are hard stops.** At every `STOP GATE`, stop, print the gate checklist with results, and wait for Abdallah to reply **"Go"**. Never start the next phase without "Go".
2. **Never expand scope.** Missing things become questions at the gate — not code.
3. **Minimal dependencies.** No library not named in BUILD_PLAN.md without asking at a gate. Banned: MoviePy, LangGraph, Kubernetes manifests, any ORM beyond SQLAlchemy, any scraping library.
4. **Pin everything.** Exact versions in `pyproject.toml`/`package.json`. External repos (freecut) are vendored at a pinned commit (documented in `VENDOR.md`), never live deps.
5. **Every Project mutation snapshots** to `project_versions`. No exceptions.
6. **Report by transcript, not narration.** At gates: files created, pytest output pasted, sample JSON, open questions. Minimal prose.
7. **Secrets** live in `.env` (git-ignored). `.env.example` documents every key.

## Hard rules (never violate, any phase)

- No trademarked brand names in style definitions, prompts, or UI copy.
- No scraping. URL import (future) uses official APIs only.
- Music only from the licensed source integrated in Phase 3; store license reference.
- Generation never runs without an approved upstream (image needs approved storyboard; video needs selected image).
- Every LLM stage call uses structured outputs validated against the Pydantic models — never free-text parsing.
- Generations are append-only — never deleted, never mutated after terminal status.
- `generation_attempts` cap of 3 per kind per scene is absolute.
- If an external API's pricing/params/model names are uncertain, check the provider's current docs — never code from memory.

## Environment notes (this machine)

- Run Python via `python3.12` explicitly (system default is 3.14).
- `node` is at `/home/linuxbrew/.linuxbrew/bin/node`.
- Docker requires `sudo` on this Pi.

## Phase status

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Repo scaffold, compose, /health, schema round-trip test | ✅ Done — Gate 0 passed 2026-07-14 |
| 1 (M0) | Storyboard engine (stages 1–4, API, CLI, styles, cost metering) | **At STOP GATE 1 — awaiting "Go"** (live CLI runs pending Abdallah's photos + OPENAI_API_KEY in backend/.env) |
| 2 (M1) | Image loop + editor seed | Not started |
| 3 (M2) | Video, QC, audio, render | Not started |
| 4 (M3) | Beta instrumentation | Not started |
