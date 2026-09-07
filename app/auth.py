import hashlib
import secrets
import os
import time
from pathlib import Path
from typing import Optional
from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse

SECRET_KEY = os.environ.get("APP_SECRET_KEY", secrets.token_hex(32))
SESSION_COOKIE = "vibte_session"
SESSION_TTL = 86400 * 7  # 7 days

HERMES_DIR = Path(os.environ.get("VIBTE_HERMES_DIR", os.path.expanduser("~") + "/.hermes"))
USERS_FILE = HERMES_DIR / "users.json"

_sessions: dict[str, dict] = {}

def _hash_password(password: str) -> str:
    return hashlib.sha256((SECRET_KEY + password).encode()).hexdigest()

def _load_users() -> dict:
    try:
        if USERS_FILE.exists():
            import json
            with open(USERS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_users(users: dict):
    HERMES_DIR.mkdir(parents=True, exist_ok=True)
    import json
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(str(USERS_FILE), 0o600)

def init_default_user():
    users = _load_users()
    if not users:
        username = os.environ.get("APP_USERNAME", "admin")
        password = os.environ.get("APP_PASSWORD", "vibte2024")
        users[username] = {
            "password_hash": _hash_password(password),
            "role": "admin",
            "created": int(time.time()),
        }
        _save_users(users)
        print(f"[auth] created default user: {username}")

def authenticate(username: str, password: str) -> bool:
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    return user.get("password_hash") == _hash_password(password)

def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": username,
        "created": time.time(),
        "expires": time.time() + SESSION_TTL,
    }
    return token

def validate_session(token: Optional[str]) -> Optional[str]:
    if not token or token not in _sessions:
        return None
    sess = _sessions[token]
    if time.time() > sess["expires"]:
        _sessions.pop(token, None)
        return None
    return sess["username"]

def logout_session(token: str):
    _sessions.pop(token, None)

def get_session_user(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    return validate_session(token)

def require_auth(request: Request):
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

def login_response(username: str, redirect_to: str = "/"):
    token = create_session(username)
    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        max_age=SESSION_TTL,
        samesite="lax",
    )
    return resp

def logout_response():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp

def add_user(username: str, password: str, role: str = "user") -> bool:
    users = _load_users()
    if username in users:
        return False
    users[username] = {
        "password_hash": _hash_password(password),
        "role": role,
        "created": int(time.time()),
    }
    _save_users(users)
    return True

def change_password(username: str, new_password: str) -> bool:
    users = _load_users()
    if username not in users:
        return False
    users[username]["password_hash"] = _hash_password(new_password)
    _save_users(users)
    return True