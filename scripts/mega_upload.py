#!/usr/bin/env python3
"""MEGA uploader using mega.py (Python library).

Supports:
1. Full account login with email/password (MEGA_EMAIL / MEGA_PASSWORD)
2. Upload to existing folders (creates missing ones)
3. Returns a public link for the uploaded file

Credentials are read from environment or ${VIBTE_HERMES_DIR}/.env.

Usage:
    python3 mega_upload.py upload <local_file> <remote_file_path> [--name NAME]
    python3 mega_upload.py list [remote_dir]
    python3 mega_upload.py status

remote_file_path examples:
    /Root/tiktok-english/Production/slug_20260101_120000.mp4
    tiktok-english/Production/slug_20260101_120000.mp4

Prints LINK=<url> on successful upload.
"""
import os
import sys
from pathlib import Path

HERMES_DIR = Path(os.environ.get("VIBTE_HERMES_DIR", os.path.join(os.path.expanduser("~"), ".hermes")))


def load_env():
    """Load env vars, preferring real env, falling back to ${HERMES_DIR}/.env."""
    env = dict(os.environ)
    env_file = HERMES_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip("'\""))
    return env


def get_client(env):
    try:
        from mega import Mega
    except ImportError as e:
        print(f"ERROR: mega.py not installed ({e}) — pip install mega.py", file=sys.stderr)
        return None
    email = env.get("MEGA_EMAIL")
    password = env.get("MEGA_PASSWORD")
    if not email or not password:
        print("ERROR: MEGA_EMAIL and MEGA_PASSWORD required", file=sys.stderr)
        return None
    try:
        m = Mega()
        c = m.login(email, password)
        print(f"INFO: logged in as {email}", file=sys.stderr)
        return c
    except Exception as e:
        print(f"ERROR: MEGA login failed: {e}", file=sys.stderr)
        return None


def normalize_rel(path):
    """/Root/tiktok-english/Production -> tiktok-english/Production"""
    s = str(path)
    for prefix in ("/Root/", "Root/", "/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip("/")


def find_child(client, name, parent_id):
    files = client.get_files()
    target = parent_id or client.root_id
    for h, n in files.items():
        if n.get("t") == 1 and (n.get("a") or {}).get("n") == name and n.get("p") == target:
            return h
    return None


def ensure_folder(client, rel_path):
    """Resolve (and create if needed) a folder path, returning its node id."""
    parent = None
    for part in [p for p in rel_path.split("/") if p]:
        node = find_child(client, part, parent)
        if node is None:
            print(f"INFO: creating folder {part}", file=sys.stderr)
            try:
                client._mkdir(part, parent)
            except Exception as e:
                print(f"ERROR: failed to create folder {part}: {e}", file=sys.stderr)
                raise
            node = find_child(client, part, parent)
            if node is None:
                raise RuntimeError(f"folder {part} not found after creation")
        parent = node
    return parent


def upload_file(client, local_path, remote_file_path, name=None):
    if not os.path.exists(local_path):
        print(f"ERROR: local file not found: {local_path}", file=sys.stderr)
        return None
    if not name:
        name = os.path.basename(remote_file_path) or os.path.basename(local_path)
    folder_rel = normalize_rel(os.path.dirname(remote_file_path))
    dest = ensure_folder(client, folder_rel) if folder_rel else None
    print(f"INFO: uploading {os.path.basename(local_path)} -> /Root/{folder_rel}/{name}", file=sys.stderr)
    try:
        resp = client.upload(local_path, dest=dest, dest_filename=name)
        link = client.get_upload_link(resp)
        return link
    except Exception as e:
        print(f"ERROR: upload failed: {e}", file=sys.stderr)
        return None


def cmd_status(env):
    client = get_client(env)
    if not client:
        return 1
    user = client.get_user()
    print(f"STATUS: connected as {user.get('email', '?')}")
    return 0


def cmd_list(env, remote_dir=""):
    client = get_client(env)
    if not client:
        return 1
    rel = normalize_rel(remote_dir)
    parent = ensure_folder(client, rel) if rel else client.root_id
    files = client.get_files()
    rows = []
    for h, n in files.items():
        if n.get("p") != parent:
            continue
        a = n.get("a") or {}
        rows.append({"handle": h, "type": "folder" if n.get("t") == 1 else "file", "name": a.get("n"), "size": n.get("s")})
    import json
    print(json.dumps(rows, indent=2))
    return 0


def cmd_upload(env, local_path, remote_file_path):
    client = get_client(env)
    if not client:
        return 1
    link = upload_file(client, local_path, remote_file_path)
    if link:
        print(f"LINK={link}")
        return 0
    return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    env = load_env()
    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status(env)
    if cmd == "list":
        remote = sys.argv[2] if len(sys.argv) > 2 else "/Root/tiktok-english/Production"
        return cmd_list(env, remote)
    if cmd == "upload":
        if len(sys.argv) < 4:
            print("Usage: mega_upload.py upload <local> <remote_file_path> [--name NAME]", file=sys.stderr)
            return 1
        local = sys.argv[2]
        remote = sys.argv[3]
        name = None
        if "--name" in sys.argv:
            name = sys.argv[sys.argv.index("--name") + 1]
        return cmd_upload(env, local, remote)
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())