#!/usr/bin/env python3
"""
TikTok Uploader — publishes rendered videos directly to TikTok via the
official TikTok Content Posting API (v2) using developer-app credentials.

Credentials (read from ~/.hermes/.env):
  TIKTOK_CLIENT_KEY     awzllk6tv7dnemn1
  TIKTOK_CLIENT_SECRET  (set by user)
  TIKTOK_REDIRECT_URI   http://localhost:8080/callback

Scopes required by the app: video.upload, video.publish

Usage:
  # One-time authorization (prints a URL; user approves, pastes the ?code=)
  python3 tiktok_uploader.py --auth-code <CODE_FROM_REDIRECT_URL>

  # Upload + publish a rendered video
  python3 tiktok_uploader.py /tmp/vibte_foo.mp4 foo-vs-bar [--caption "..."] [--privacy PRIVATE|PUBLIC_TO_EVERYONE]

Exit codes:
  0  published / nothing to do
  1  hard failure
  2  needs authorization (auth URL printed)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Load ~/.hermes/.env (cron runs don't source it) ──
HERMES_DIR = os.environ.get("VIBTE_HERMES_DIR", os.path.join(os.path.expanduser("~"), ".hermes"))

def _load_env():
    env_path = os.path.join(HERMES_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v

_load_env()

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")
TOKEN_FILE = os.path.join(HERMES_DIR, "tiktok_token.json")
STATE_FILE = os.path.join(HERMES_DIR, "tiktok_uploads.json")

API_BASE = "https://open.tiktokapis.com/v2"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload,video.publish"

TAGS = [
    "#learnenglish", "#englishgrammar", "#englishvocabulary",
    "#commonlyconfused", "#esl", "#englishlearning", "#vibte",
]
MAX_SIZE = 287.6 * 1024 * 1024  # TikTok file limit


def log(msg):
    print(f"[tt-post] {msg}", flush=True)


# ── Token store ──
def load_tokens():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_tokens(tokens):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)


def _tok_request(form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"error": {"code": e.code, "message": body}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def get_access_token():
    """Return a usable access token, refreshing as needed. None if we need re-auth."""
    tokens = load_tokens()
    access = tokens.get("access_token", "")
    exp = tokens.get("expires_at", 0)

    if access and exp and time_now() < exp - 300:
        return access

    refresh = tokens.get("refresh_token", "")
    if not refresh:
        return None

    r = _tok_request({
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })
    if "access_token" not in r:
        log(f"refresh failed: {r.get('error', r)}")
        return None

    tokens["access_token"] = r["access_token"]
    tokens["refresh_token"] = r.get("refresh_token", refresh)
    tokens["expires_at"] = time_now() + int(r.get("expires_in", 86400))
    save_tokens(tokens)
    log("access token refreshed")
    return tokens["access_token"]


def exchange_code(code):
    r = _tok_request({
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })
    if "access_token" not in r:
        log(f"code exchange error: {r.get('error', r)}")
        return False
    save_tokens({
        "access_token": r["access_token"],
        "refresh_token": r.get("refresh_token", ""),
        "expires_at": time_now() + int(r.get("expires_in", 86400)),
        "open_id": r.get("open_id", ""),
        "scope": r.get("scope", ""),
    })
    log("✅ Tokens saved to ~/.hermes/tiktok_token.json")
    return True


def print_auth_url():
    params = urllib.parse.urlencode({
        "client_key": CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": "vibte-tt",
    })
    url = f"{AUTH_URL}?{params}"
    log("")
    log("🔑 One-time TikTok authorization required")
    log("    1) Open this URL and approve the app:")
    log(f"       {url}")
    log("    2) After approving you are redirected to a URL containing")
    log("       ?code=XXXX  — send that full code to the agent who runs:")
    log("       python3 tiktok_uploader.py --auth-code XXXX")
    log("")
    return url


def time_now():
    import time as _t
    return _t.time()


# ── API helpers ──
def api_post(path, payload, token):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode())}
        except Exception:
            return {"error": {"code": e.code, "message": str(e)}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def put_video(url, path):
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Content-Type", "video/mp4")
    req.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return True, resp.read().decode()
    except urllib.error.HTTPError as e:
        return False, e.read().decode()[:500]
    except Exception as e:
        return False, str(e)


# ── Caption ──
def build_caption(episode_id=None, extra=None):
    if extra:
        cap = extra
    elif episode_id:
        # foo-vs-bar -> "Foo vs Bar — Know the difference!"
        parts = episode_id.replace("-vs-", " vs ").split()
        title = " ".join(w[0].upper() + w[1:] for w in parts)
        cap = f"{title} — Know the difference!"
    else:
        cap = "Learn English — Know the difference!"
    return f"{cap}\n\n{' '.join(TAGS)}"[:2200]


# ── Publish flow ──
def publish_video(video_path, caption, token, privacy="PUBLIC_TO_EVERYONE"):
    if not os.path.exists(video_path):
        log(f"❌ video not found: {video_path}")
        return None
    size = os.path.getsize(video_path)
    if size > MAX_SIZE:
        log(f"❌ too large: {size/1048576:.1f} MB (limit 287.6)")
        return None

    # 1) init
    log(f"📦 init upload {size/1048576:.1f} MB ...")
    r = api_post("/post/publish/video/init/", {
        "post_info": {
            "title": caption[:100],
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    }, token)
    if "error" in r:
        log(f"❌ init failed: {r['error']}")
        return None
    data = r.get("data", {})
    upload_id = data.get("upload_id")
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    log(f"   upload_id={upload_id} publish_id={publish_id}")

    # 2) upload bytes
    ok, body = put_video(upload_url, video_path)
    if not ok:
        # SDK-style: verify content
        verify = api_post("/post/publish/video/check/", {
            "publish_id": publish_id,
            "upload_id": upload_id,
        }, token)
        log(f"   put failed: {body} | check: {verify}")
        return None

    # 3) confirm publish
    log("   confirming publish ...")
    conf = api_call("/post/publish/video/", {
        "publish_id": publish_id,
        "upload_id": upload_id,
    }, token)
    log(f"   confirm response: {json.dumps(conf)[:300]}")

    # 4) status
    time.sleep(2)
    st = api_call("/post/publish/status/fetch/", {
        "publish_id": publish_id,
    }, token)
    log(f"   status: {json.dumps(st)[:400]}")

    return {"publish_id": publish_id, "init": r, "confirm": conf, "status": st}


def api_call(path, payload, token):
    return api_post(path, payload, token)


def main():
    p = argparse.ArgumentParser(description="TikTok video uploader")
    p.add_argument("video", nargs="?", help="path to MP4 to publish")
    p.add_argument("episode_id", nargs="?", help="episode slug -> caption")
    p.add_argument("--caption", default=None, help="overrides auto caption")
    p.add_argument("--privacy", default="PUBLIC_TO_EVERYONE")
    p.add_argument("--code", default=None, help="OAuth code from redirect URL (one-time)")
    a = p.parse_args()

    if a.code:
        sys.exit(0 if exchange_code(a.code) else 1)

    if not CLIENT_KEY or not CLIENT_SECRET:
        log("⚠️  TIKTOK_CLIENT_KEY/SECRET not set — skipping (producer continues)")
        return 0

    token = get_access_token()
    if not token:
        print_auth_url()
        return 2

    if not a.video:
        log("no video path provided — nothing to do")
        return 0

    cap = build_caption(a.episode_id, a.caption)
    res = publish_video(a.video, cap, token, a.privacy)
    if not res:
        return 1

    # record
    st = load_state_file()
    st["uploads"].append({
        "video": a.video, "episode_id": a.episode_id,
        "publish_id": res.get("publish_id"),
        "caption": cap, "ts": now_iso(),
    })
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2)
    print(json.dumps(res, indent=2))
    return 0


def load_state_file():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"uploads": []}


def now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


if __name__ == "__main__":
    main()