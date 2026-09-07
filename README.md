# Vibte TikTok Pipeline — Docker + Web UI

**Automated English learning video generator for TikTok** — Creates vertical 9:16 videos comparing commonly confused English words (e.g., "affect vs effect", "their vs there vs they're") with TTS narration, uploads to MEGA cloud storage, and tracks production in Supabase.

## What It Does

| Feature | Description |
|---------|-------------|
| **Video Rendering** | 6-slide vertical videos (1080×1920, 30fps) using Pillow + ffmpeg |
| **TTS Narration** | edge-tts via 9Router gateway (GuyNeural voice, configurable) |
| **Episode Library** | 79 pre-built episodes (A2–C2 CEFR levels) in `scripts/episodes.json` |
| **Duplicate Prevention** | Checks Supabase `vibte_videos` table before rendering — never repeats |
| **Vocab Generation** | Creates new episodes from Oxford 5000 word list (offline, no API calls) |
| **MEGA Upload** | Auto-uploads rendered MP4s to `/Root/tiktok-english/Production/` |
| **TikTok Publishing** | Optional: publishes to TikTok via Content Posting API v2 |
| **Web UI** | FastAPI + vanilla JS — Producer, Vocab, Jobs, Artifacts, MEGA panels |

## Components

```
tiktok-pipeline-app/
├── app/                      # FastAPI application
│   ├── main.py              # API endpoints, auth, job management
│   ├── pipeline.py          # Core logic: render, producer, MEGA, vocab, Supabase
│   ├── wizard.py            # 5-step custom episode creation wizard
│   ├── ai.py                # LLM episode generation via 9Router
│   ├── auth.py              # Session-based authentication
│   ├── jobs.py              # Background job manager (threaded)
│   └── static/              # Web UI (index.html, login.html)
├── scripts/                  # CLI tools & templates
│   ├── episodes.json        # 79 pre-defined episode definitions
│   ├── vibte_producer.py    # CLI: pick → render → upload → save to Supabase
│   ├── generate_vibte_video.py  # Video rendering template (Pillow + ffmpeg)
│   ├── vocab_source.py      # Oxford 5000 CSV → new episodes
│   ├── mega_upload.py       # Python MEGA upload (no CLI needed)
│   └── tiktok_uploader.py   # TikTok Content Posting API v2
├── docker-compose.yml       # Service definition (uses .env for secrets)
├── Dockerfile               # Python 3.10 + ffmpeg + fonts + dependencies
├── requirements.txt         # Python dependencies
└── .env.example             # Environment variable template
```

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/vibte740/tiktok-pipeline-app.git
cd tiktok-pipeline-app
cp .env.example .env
# Edit .env with your credentials (MEGA, Supabase, 9Router, etc.)

# 2. Build and start
docker compose up -d --build

# 3. Open UI
open http://localhost:8000
# Login: admin / vibte2024 (change in .env!)
```

## Configuration (.env)

| Variable | Required | Description |
|----------|----------|-------------|
| `NINEROUTER_URL` | Yes | TTS/LLM gateway (e.g., `http://host.docker.internal:20128/v1`) |
| `NINEROUTER_API_KEY` | Yes | 9Router API key |
| `AI_MODEL` | No | LLM model (default: `cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast`) |
| `MEGA_EMAIL` | Yes | MEGA account email |
| `MEGA_PASSWORD` | Yes | MEGA account password |
| `MEGA_BASE` | No | Remote folder (default: `/Root/tiktok-english/Production`) |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key |
| `SUPABASE_TABLE` | No | Table name (default: `vibte_videos`) |
| `OXFORD_CSV_URL` | No | Oxford 5000 CSV (default: GitHub raw) |
| `APP_USERNAME` | No | Web UI username (default: `admin`) |
| `APP_PASSWORD` | No | Web UI password (default: `vibte2024`) |
| `APP_SECRET_KEY` | Yes | Session signing key (generate secure random) |

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/episodes` | All 79 episodes with `produced: true/false` flag |
| `GET` | `/api/pick` | Next unproduced episode (weighted by level) |
| `POST` | `/api/render` | Render episode — **returns 409 if already in Supabase** |
| `POST` | `/api/producer` | Full pipeline: pick → render → MEGA → Supabase |
| `POST` | `/api/vocab/generate` | Generate new episode from Oxford 5000 |
| `GET` | `/api/database/status` | Supabase overview (produced/remaining counts) |
| `GET` | `/api/database/check/{id}` | Check if single episode exists in DB |
| `POST` | `/api/wizard/step1` | Create draft, check duplicates |
| `POST` | `/api/wizard/step3/render` | Render with duplicate check |
| `GET` | `/api/artifacts/{slug}/video.mp4` | Download rendered video |

## Database Schema (Supabase)

```sql
CREATE TABLE vibte_videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id TEXT NOT NULL UNIQUE,      -- e.g., "affect-vs-effect"
    level TEXT NOT NULL,                   -- "LEVEL B1"
    word1 TEXT NOT NULL,                   -- "AFFECT"
    word2 TEXT NOT NULL,                   -- "EFFECT"
    mega_path TEXT,                        -- "/Root/tiktok-english/Production/..."
    mega_link TEXT,                        -- "https://mega.nz/..."
    tiktok_published BOOLEAN DEFAULT FALSE,
    render_seconds INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │────▶│  FastAPI    │────▶│  Supabase   │
│   (UI)      │     │  :8000      │     │  (PostgreSQL)│
└─────────────┘     └─────────────┘     └─────────────┘
                          │                     │
                          ▼                     ▼
                   ┌─────────────┐       ┌─────────────┐
                   │   9Router   │       │    MEGA     │
                   │  (TTS/LLM)  │       │  (Storage)  │
                   └─────────────┘       └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  TikTok API │  (optional)
                   └─────────────┘
```

## Volumes (Persisted)

| Volume | Mount | Contents |
|--------|-------|----------|
| `vibte-work` | `/var/vibte` | Working root |
| `vibte-hermes` | `/var/vibte/hermes` | MEGA creds, TikTok tokens, producer state, users, wizard drafts |

## Related Repository

**Infrastructure Stack**: [vibte-agent-stack](https://github.com/vibte740/vibte-agent-stack) — Runs 9Router, Headroom, Hermes, Omniroute behind Nginx reverse proxy.

---

*Part of the Vibte English Learning Platform*