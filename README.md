# Vibte TikTok Pipeline — Docker + Web UI

Containerized web app exposing every action of the `vibte-pipeline` skill:
**pick episodes, render videos, upload to MEGA, publish to TikTok,
generate new episodes, and inspect registry/state.**

## Quick Start

```bash
# Build the image (includes ffmpeg, fonts, mega.py for MEGA upload)
docker compose build

# Start the stack
docker compose up -d

# Open the UI
open http://localhost:8000
```

### First-Time Setup (in the web UI → MEGA panel)

| Field | Value |
|-------|-------|
| MEGA Email | your MEGA account email |
| MEGA Password | your MEGA account password |
| Upload Base Folder | `/Root/tiktok-english/Production` |

Click **Save MEGA Credentials**. The password is stored in the persistent
`hermes` Docker volume and never leaves the container.

### Logging In

The web app is password-protected. Open `http://<host>:8000` and you'll be
redirected to a login page. Default credentials:

| Field | Default |
|-------|---------|
| Username | `admin` |
| Password | `vibte2024` |

**Change them before exposing publicly** via environment variables:

```bash
docker compose run --rm -e APP_USERNAME=YOUR_USER -e APP_PASSWORD=YOUR_PASS ... 
# or set in docker-compose.yml:
#   APP_USERNAME=${APP_USERNAME:-admin}
#   APP_PASSWORD=${APP_PASSWORD:-vibte2024}
```

Sessions last 7 days and are cleared with the **Logout** button in the header.

## Actions Available via the UI

| Panel | What It Does |
|-------|-------------|
| **Producer** | Pick the next unproduced episode (weighted by level), render a full 6-slide MP4, upload to MEGA, mark complete. Supports `--render-only` and `--no-upload` toggles. |
| **TikTok** | One-time OAuth authorization, code exchange, then publish any rendered MP4 to TikTok. |
| **MEGA** | List Production folder, upload arbitrary files, configure credentials. Uses Python `mega.py` when no CLI is installed. |
| **Vocab** | Generate a new episode draft from the Oxford 5000 word list + Free Dictionary API (B1–C2 levels). |
| **State & Registry** | View completed episodes, the production registry (per render), and the drafts registry. |
| **Jobs** | Live-updating list of background jobs with streaming logs and artifact links. |
| **Artifacts** | Download rendered MP4s, contact sheets, and episode JSONs. |

## Architecture

```
Browser ←→ FastAPI (uvicorn, port 8000)
               │
               ├── render_job    → Pillow frames → edge-tts (mock or 9Router) → ffmpeg MP4
               ├── producer_job  → pick_next → render → MEGA upload → ledger write
               ├── mega_upload   → mega.py (Python) or megaput/mega-put (CLI)
               └── tiktok_publish → TikTok Content Posting API v2
```

All long-running work runs in background threads; the UI polls `/api/jobs/{id}`.

## Configuration

Environment variables (set in `docker-compose.yml` or override with `docker run -e`):

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `NINEROUTER_URL` | Yes | `http://127.0.0.1:20128/v1` | TTS gateway. Set to `http://host.docker.internal:20128/v1` if 9Router runs on the Docker host. |
| `MEGA_EMAIL` | Yes | — | MEGA account email. Can also be saved via the web UI. |
| `MEGA_PASSWORD` | Yes | — | MEGA account password. Saved via web UI. |
| `MEGA_BASE` | No | `/Root/tiktok-english/Production` | Remote MEGA folder for rendered videos. |
| `SUPABASE_URL` | No | — | For remote episode tracking. Falls back to local ledger. |
| `SUPABASE_SERVICE_KEY` | No | — | Supabase anon key. |
| `TIKTOK_CLIENT_KEY` | No | — | TikTok developer app client key. |
| `TIKTOK_CLIENT_SECRET` | No | — | TikTok developer app client secret. |

### MEGA (without a host CLI)

The image ships `mega.py` (Python). No `megaput`/`mega-cmd` required.
Credentials are set via the web UI or environment variables.

### TTS via 9Router

If you have [9Router](https://github.com/vibte740/9router) running on the
Docker host, set `NINEROUTER_URL=http://host.docker.internal:20128/v1`.

Without 9Router the pipeline still works but produces **silent** placeholder
audio (3 s per slide) — useful for layout testing.

## Volumes

| Mount | Purpose |
|-------|---------|
| `vibte-hermes` | MEGA credentials, TikTok tokens, producer state ledger |
| `vibte-work` | Rendered artifacts (MP4s, contact sheets, episode JSON) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/episodes` | All episodes with produced status |
| `GET` | `/api/pick` | Next episode (weighted by level) |
| `POST` | `/api/producer` | Full producer job (`episode_id`, `render_only`, `no_upload`) |
| `POST` | `/api/render` | Render-only draft job |
| `POST` | `/api/vocab/generate` | Generate episode from Oxford 5000 |
| `GET` | `/api/mega/status` | MEGA connection status |
| `PUT` | `/api/mega/config` | Save MEGA credentials |
| `GET` | `/api/tiktok/status` | TikTok token status |
| `GET` | `/api/tiktok/auth-url` | Generate OAuth URL |
| `POST` | `/api/tiktok/code` | Exchange OAuth code |
| `POST` | `/api/tiktok/publish` | Publish video to TikTok |
| `GET` | `/api/jobs` | List all background jobs |
| `GET` | `/api/jobs/{id}` | Job detail with logs |
| `GET` | `/api/artifacts/{slug}/{file}` | Download rendered artifacts |

## Smoke Test

```bash
# Start a mock 9Router inside the container (no real TTS needed)
docker run -it --rm -p 8000:8000 -e NINEROUTER_URL=http://127.0.0.1:9999/v1 \
  -e MEGA_EMAIL=you@gmail.com -e MEGA_PASSWORD=secret \
  vibte-pipeline bash -c \
  "python3 /app/tests/mock_tts.py 9999 & sleep 1 && uvicorn app.main:app --port 8000"

# Then hit the API
curl http://localhost:8000/api/pick
curl -X POST http://localhost:8000/api/producer -H 'Content-Type: application/json' \
  -d '{"render_only":true}'
```

## Manual MEGA Cleanup

During development a duplicate `tiktok-english/Production` folder (`y45BHKAC`)
and a test file (`vibte_test_upload.mp4`) were created.
Delete them manually in https://mega.nz — the delete API is not supported
by the Python library in all environments.
