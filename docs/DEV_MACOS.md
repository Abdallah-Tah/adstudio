# Developing on macOS (Apple Silicon)

Tested target: MacBook M-series, 24 GB RAM. Everything runs natively on arm64.

## 1. Prerequisites

```bash
brew install python@3.12 pnpm ffmpeg
brew install --cask docker        # or OrbStack; either provides `docker compose`
```

## 2. Clone and configure

```bash
git clone https://github.com/Abdallah-Tah/adstudio.git
cd adstudio
cp backend/.env.example backend/.env   # fill in the keys you have
```

Keys and what stops working without them:

| Key | Needed for |
|---|---|
| `OPENAI_API_KEY` | storyboard stages, image generation, image identity QC, cross-scene check, AI photo pre-check |
| `ANTHROPIC_API_KEY` | video QC (keyframe + frame-drift) |
| `FAL_KEY` | video generation |
| `ELEVENLABS_API_KEY` | voiceover |

You can develop the upload flow and storyboard with only `OPENAI_API_KEY`;
production readiness will list anything missing rather than failing mid-run.

## 3. Infrastructure (Postgres, Redis, MinIO)

```bash
docker compose up -d
```

## 4. Backend API + worker

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --port 8300 --reload            # terminal 1
celery -A app.workers.celery_app worker -l info      # terminal 2
```

First upload triggers a one-time download of the rembg (u2net) model weights
to `~/.u2net/` — allow ~170 MB.

Run the tests: `pytest -q`

## 5. Frontend

```bash
cd frontend
pnpm install
pnpm dev        # http://localhost:3000, expects the API on :8300
```

## 6. Trying the customer flow

1. Open **Create Ad**, drop product photos. The pre-check runs immediately and
   labels each photo **Great / Usable / Replace** with tips (plain background,
   fill the frame, no marketing text). You cannot continue while every photo
   is marked Replace.
2. On the Review step pick **Auto-pilot** (default) or **Manual review**.
3. Auto-pilot: storyboard is approved automatically, every scene image is
   generated and identity-checked, the cross-scene consistency gate runs, and
   production starts — the project page shows a live banner with a **Pause**
   button. If identity ever fails, auto-pilot pauses with a plain-language
   reason and hands you the normal manual editor.
4. Manual: identical to before — nothing about the manual flow changed.
