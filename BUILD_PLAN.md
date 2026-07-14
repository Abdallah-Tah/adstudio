# AI Ad Studio — Build Plan for Claude Code

You are implementing **AI Ad Studio v3**: a pipeline that turns product photos into a platform-ready TikTok ad (9:16 MP4) with human review gates between expensive stages. This document is the complete, authoritative plan. Follow it phase by phase.

## Working agreement (read first, applies to every phase)

1. **STOP gates are hard stops.** At every line marked `STOP GATE`, stop working, print the gate checklist with your results, and wait. Abdallah replies **"Go"** to proceed. Do not start the next phase without "Go".
2. **Never expand scope.** If something seems missing, list it at the gate as a question — do not build it.
3. **Minimal dependencies.** Do not add a library that isn't named in this plan without asking at a gate. Explicitly banned: MoviePy, LangGraph, Kubernetes manifests, any ORM beyond SQLAlchemy, any scraping library.
4. **Pin everything.** Exact versions in `pyproject.toml`/`package.json`. External repos (freecut) are vendored at a pinned commit, never installed as live deps.
5. **Every mutation of a Project snapshots it** to `project_versions`. No exceptions.
6. **Report by transcript, not narration.** At each gate, show: files created, tests passing (paste pytest output), sample JSON output, and open questions. Keep prose minimal.
7. **Secrets** live in `.env` (git-ignored). Ship `.env.example` with every key documented.

---

## Phase 0 — Repo scaffold

Create a monorepo:

```
ad-studio/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry
│   │   ├── schema.py            # Pydantic models (Section A below, verbatim)
│   │   ├── db.py                # SQLAlchemy: projects, project_versions, generations
│   │   ├── snapshots.py         # snapshot-on-mutation helper
│   │   ├── stages/
│   │   │   ├── analysis.py      # Stage 1: photos+desc → ProductProfile
│   │   │   ├── brief.py         # Stage 2: profile+user input → CreativeBrief
│   │   │   ├── strategy.py      # Stage 3: brief → Strategy
│   │   │   ├── storyboard.py    # Stage 4: strategy → list[Scene] (intent only)
│   │   │   ├── images.py        # Stage 5 (Phase 2)
│   │   │   ├── video.py         # Stage 6 (Phase 3)
│   │   │   ├── qc.py            # Stage 7 (Phase 3)
│   │   │   ├── audio.py         # Stage 8 (Phase 3)
│   │   │   └── render.py        # Stage 9 (Phase 3)
│   │   ├── compiler/            # prompt compiler: Scene intent → provider prompt
│   │   │   ├── base.py          # VideoEngine/ImageEngine Protocols + capabilities
│   │   │   └── (one target per provider, added per phase)
│   │   ├── providers/           # thin API clients, one file each
│   │   ├── workers/             # Celery tasks: one per stage
│   │   └── storage.py           # R2/S3 client (boto3, works with MinIO locally)
│   ├── tests/
│   ├── vendor/freecut/          # Phase 3: vendored helpers, pinned commit noted in VENDOR.md
│   ├── pyproject.toml
│   └── .env.example
├── frontend/                    # Phase 2: Next.js + Tailwind + shadcn/ui
├── docker-compose.yml           # postgres, redis, minio
├── CLAUDE.md                    # summarize the Working agreement + phase status here
└── BUILD_PLAN.md                # this file
```

Stack (pinned latest stable): Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Celery, redis, boto3, psycopg. Postgres stores `Project` as JSONB plus a `project_versions` table: `(version_id, project_id, snapshot jsonb, actor text, reason text, created_at timestamptz)`.

`.env.example` keys: `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_KEY`, `S3_SECRET`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (QC vision, Phase 3), `ELEVENLABS_API_KEY` (Phase 3), `VIDEO_ENGINE_API_KEY` (Phase 3), `MUSIC_LIBRARY_KEY` (Phase 3).

Acceptance: `docker compose up` brings up pg/redis/minio; `uvicorn` serves `/health`; `pytest` runs green on a schema round-trip test.

**STOP GATE 0** — show tree, compose output, green pytest. Wait for "Go".

---

## Phase 1 (= M0) — Storyboard engine

Goal: photos + description in → validated `Project` JSON out (brief, strategy, scene intents). No image/video generation yet.

### Tasks

1. **schema.py**: implement Section A verbatim. Add JSON Schema export endpoint `GET /schema` (Pydantic `model_json_schema`).
2. **Stages 1–4** as pure functions `run(input) -> model`, each making **one** LLM structured-output call (OpenAI, `response_format` with the Pydantic model). No agent frameworks. Prompts live in `stages/prompts/` as versioned `.md` files — Abdallah will edit these directly.
   - Stage 1 (analysis): images + description → `ProductProfile`. Vision call; extract only what's visible/stated, `key_benefits` capped at 3.
   - Stage 2 (brief): profile + user's audience/offer/CTA inputs → `CreativeBrief`. This encodes what the USER wants; where the user gave a value, pass it through untouched.
   - Stage 3 (strategy): brief + profile → `Strategy`. Hook must be executable in 1.5s of footage. Script length must fit `target_duration_s` at ~2.5 words/sec.
   - Stage 4 (storyboard): strategy → `list[Scene]` intents. Durations must sum to `target_duration_s` ±3s (validator enforces). Each `vo_line` must be a contiguous slice of `Strategy.script`.
3. **API**: `POST /projects` (multipart: photos + form fields) runs stages 1–4 synchronously for now, snapshots after each stage, returns the Project. `GET /projects/{id}`, `GET /projects/{id}/versions`, `POST /projects/{id}/scenes/{sid}` (PATCH intent fields → snapshot, and bump nothing else).
4. **CLI** `python -m app.cli create ./photos/ "description..." --audience "..." --offer "..." --style minimal_tech` → pretty-prints the Project and writes `project.json`.
5. **Style definitions**: `styles.py` with the 5 styles (minimal_tech, warm_lifestyle, bold_energy, studio_luxury, ugc_handheld), each a dict of visual vocabulary the storyboard prompt consumes. Descriptive language only — reject any trademarked brand name in style content.
6. **Tests**: schema round-trip; duration validator; vo_line-slice validator; snapshot created per mutation; one recorded-fixture test per stage (use pytest + respx/vcr-style fixtures so tests don't hit live APIs).
7. **Cost metering**: wrap every LLM call, record token cost into `CostLedger` (analysis/strategy buckets).

### Exit criteria (print at gate)
- [ ] `pytest` green, coverage of validators shown
- [ ] CLI run on 3 sample products (Abdallah supplies photos) produces valid Projects
- [ ] Snapshots visible via `GET /versions` after edits
- [ ] Cost per storyboard printed (expect < $0.10)

**STOP GATE 1** — Abdallah reviews storyboard quality on 10 products before any image spend. Prompt iteration happens here. Wait for "Go".

---

## Phase 2 (= M1) — Image loop + editor seed

Goal: reference-conditioned images per scene, and the first real editor UI.

### Tasks

1. **Segmentation**: `rembg` (pinned) on upload → cutout PNGs stored as `reference` assets in `ProductProfile.reference_images` alongside the originals. If rembg output is poor (alpha coverage < 5% or > 95%), keep original and flag `segmentation_failed` in notes.
2. **Prompt compiler — image target**: `compiler/gpt_image.py`. Input: Scene intent + style + ProductProfile. Output: provider prompt where text carries ONLY the delta (camera/lighting/action/environment); product identity comes from reference images passed via the API's image-conditioning input. Record everything in a `Generation` (provider, model, prompt, prompt_hash = sha256 of compiled prompt, reference_assets, seed if supported, cost).
3. **Celery**: stage 5 becomes an async task per scene; `POST /projects/{id}/scenes/{sid}/generate-image` enqueues; retry cap 3 enforced via `generation_attempts`; failures set status and never auto-retry past cap.
4. **Staleness**: editing intent recomputes prompt_hash; API response marks generations whose hash no longer matches as `stale: true` (computed, not stored).
5. **Editor v0** (Next.js + Tailwind + shadcn/ui):
   - Scene rail: thumbnails (selected generation), status badges, drag-reorder (writes `order`, snapshots).
   - Intent panel: edit action/camera/lighting/vo_line/caption/duration → PATCH → stale flags appear.
   - Preview pane: selected image large.
   - Buttons: Regenerate scene · Select generation (compare list of all Generations for the scene) · Approve.
   - Cost ledger visible at all times (per-stage + total).
   - No auth in v0.1 — single-user, localhost.
6. **Tests**: compiler output snapshot tests; staleness computation; retry cap; an integration test that runs a full project through mocked image generation.

### Exit criteria
- [ ] 10 test products: product identity holds across all scenes for ≥8 (Abdallah judges from a generated contact sheet the CLI produces: `python -m app.cli contact-sheet <project_id>`)
- [ ] Regenerate-one-scene works without touching others; history preserved
- [ ] Editor usable end-to-end by someone with no instructions
- [ ] Per-image cost recorded and shown

**STOP GATE 2** — identity consistency is the moat check. Also confirm here: which video engine for Phase 3 (default proposal: the engine with image-to-video + reference conditioning at the best $/sec at time of build — present 2–3 current options with pricing pulled from their docs, ranked). Wait for "Go" + engine choice.

---

## Phase 3 (= M2) — Video, QC, audio, render

Goal: end-to-end draft ad ≤15 min with automated QC.

### Tasks

1. **Prompt compiler — video target** for the chosen engine: image-to-video from the scene's selected image, duration from intent, motion described from action/camera. One target only; the `VideoEngine` Protocol in `compiler/base.py` is the seam for engine #2 later:
   ```python
   class VideoEngine(Protocol):
       capabilities: EngineCapabilities  # max_duration_s, ref_conditioning, cost_per_s
       def generate(self, image: AssetRef, prompt: str,
                    duration_s: float, seed: int | None) -> AssetRef: ...
   ```
2. **QC stage (claude-real-video)**: `pip install claude-real-video` at a pinned version. Per generated clip and on the final render:
   - `process(clip_path, out_dir, ...)` with transcription off → scene-change key frames.
   - Vision call (Claude, `ANTHROPIC_API_KEY`): key frames + 2 product reference images + the scene's intent → strict JSON verdict `{identity_ok: bool, artifacts: bool, caption_legible: bool | null, notes: str}`.
   - Fail → `status=qc_rejected`, `qc_notes` filled, auto-retry only if `generation_attempts < 3`. QC spend metered to `CostLedger.qc`.
3. **Voiceover**: ElevenLabs, one call for the full script, then split per scene by `vo_line` timing (use word timestamps from the API; if unavailable, force-align with whisper-timestamped — ask at gate before adding that dep).
4. **Music**: integrate ONE commercially-licensed source (present options at Gate 2: a stock library API vs. a music-gen commercial tier; Abdallah picks). Store license reference with the asset.
5. **Render worker** (FFmpeg only). Vendor from freecut at a pinned commit into `vendor/freecut/` (document commit hash + files taken in `VENDOR.md`):
   - EDL-driven assembly of selected video clips in scene order
   - 30ms audio fades at every cut
   - ASS subtitle generation for caption styles (bounce = 2-word uppercase chunks with word timing; highlight; plain), burned via ffmpeg
   - VO + music mix (music ducked -12dB under VO)
   - transitions: cut/fade/whip per intent
   - Output: 1080×1920 H.264 MP4 + final QC pass via the same crv loop
6. **Pipeline orchestration**: `POST /projects/{id}/produce` runs stages 6→9 as a Celery chain, respecting gates: refuses unless every scene has an approved (selected) image. Progress via `GET /projects/{id}/status` (per-scene statuses + current stage).
7. **Editor additions**: video preview per scene, final render player with scrub, "Produce" button gated on approvals, QC verdicts surfaced on rejected generations.
8. **Instrumentation**: per-stage wall-clock and cost logged per project; `GET /projects/{id}/report` returns the numbers Gate 3 needs.

### Exit criteria
- [ ] End-to-end: photos → final MP4 in ≤15 min wall clock (excluding human gate time)
- [ ] QC catches ≥80% of defects Abdallah flags on a 20-clip labeled sample (build `python -m app.cli qc-eval` to compute this)
- [ ] Full per-stage cost report for a 30s ad — the real unit economics number
- [ ] 5 finished ads Abdallah would actually post
- [ ] Retry caps verified: a deliberately bad prompt cannot spend past cap

**STOP GATE 3** — pricing and retry policy decided here from the real numbers. Wait for "Go".

---

## Phase 4 (= M3) — Beta instrumentation

Only after Gate 3 "Go".

1. Minimal auth (email magic link) + per-user projects.
2. Event log for analytics (this is NOT event sourcing — it's a metrics table): gate abandonment, regen rate per scene position, QC rejection reasons, export events.
3. Deploy: single VPS, Docker Compose, Caddy for TLS. No Kubernetes.
4. `python -m app.cli beta-report` summarizing the M3 exit metrics: users who exported, regen hotspots, cost per exported ad.

**STOP GATE 4** — beta learnings decide M4 order (variant compare UI → second engine → Shopify import → Reels/Shorts export → UGC mode on freecut). Do not pre-build any of these.

---

## Hard rules (never violate, any phase)

- No trademarked brand names anywhere in style definitions, prompts, or UI copy (no "Apple style", "Nike", "Pixar", etc.).
- No scraping. Future URL import uses official APIs only.
- Music only from the licensed source integrated in Phase 3; store the license reference.
- Video/image generation never runs on a scene without an approved upstream (image needs approved storyboard state; video needs selected image).
- Every LLM stage call uses structured outputs validated against the Pydantic models — never free-text parsing.
- Every Project mutation snapshots. Every Generation is append-only — never deleted, never mutated after terminal status.
- `generation_attempts` cap of 3 per kind per scene is absolute.
- If an external API's pricing, parameters, or model names are uncertain, check the provider's current docs before coding against them — do not code from memory.

---

## Section A — schema.py (implement verbatim)

```python
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional


class AssetRef(BaseModel):
    asset_id: str
    kind: Literal["image", "video", "audio", "reference", "render"]
    uri: str
    generated_from: Optional[str] = None      # scene_id
    reference_assets: list[str] = []          # asset_ids conditioned in
    created_at: str                           # ISO 8601


class Generation(BaseModel):
    generation_id: str
    scene_id: str
    kind: Literal["image", "video"]
    provider: str
    model: str
    prompt: str
    prompt_hash: str                          # sha256 of compiled prompt
    seed: Optional[int] = None
    reference_assets: list[str] = []
    status: Literal["queued", "running", "succeeded",
                    "failed", "qc_rejected"] = "queued"
    qc_notes: Optional[str] = None
    cost_cents: int = 0
    asset: Optional[AssetRef] = None
    created_at: str


class ProductProfile(BaseModel):
    name: str
    brand: str
    category: str
    colors: list[str]
    materials: list[str]
    key_benefits: list[str] = Field(max_length=3)
    audience: str
    reference_images: list[AssetRef]


class CreativeBrief(BaseModel):
    audience: str
    pain_points: list[str]
    desired_emotion: str
    tone: str
    brand_voice: str
    offer: str
    cta: str
    platform: Literal["tiktok"] = "tiktok"
    target_duration_s: float = Field(ge=10, le=60)


class Strategy(BaseModel):
    hook: str
    angle: Literal["problem_solution", "demo", "social_proof",
                   "before_after", "curiosity"]
    emotional_trigger: str
    script: str
    style_id: str
    scene_count: int = Field(ge=3, le=12)


class Scene(BaseModel):
    scene_id: str
    order: int
    duration_s: float = Field(ge=0.5, le=8.0)
    camera: str
    lighting: str
    action: str
    vo_line: Optional[str] = None
    caption: Optional[str] = None
    caption_style: Literal["bounce", "highlight", "plain"] = "bounce"
    transition_out: Literal["cut", "fade", "whip"] = "cut"
    generations: list[Generation] = []
    selected_image: Optional[str] = None      # generation_id
    selected_video: Optional[str] = None      # generation_id
    generation_attempts: int = 0

    @property
    def status(self) -> str:
        if self.selected_video: return "video_ready"
        if self.selected_image: return "image_ready"
        return "draft"


class CostLedger(BaseModel):
    analysis: int = 0
    strategy: int = 0
    images: int = 0
    videos: int = 0
    voice: int = 0
    music: int = 0
    qc: int = 0
    render: int = 0

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class Project(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    project_id: str
    created_at: str
    product: ProductProfile
    brief: CreativeBrief
    strategy: Strategy
    scenes: list[Scene] = Field(min_length=3, max_length=12)
    voiceover: Optional[Generation] = None
    music: Optional[AssetRef] = None
    final_render: Optional[AssetRef] = None
    cost: CostLedger = CostLedger()

    @model_validator(mode="after")
    def duration_matches_brief(self):
        total = sum(s.duration_s for s in self.scenes)
        assert abs(total - self.brief.target_duration_s) <= 3.0, \
            f"scene durations sum to {total}s, brief wants {self.brief.target_duration_s}s"
        return self
```

---

## Start

Begin with Phase 0. Report at STOP GATE 0 and wait for "Go".
