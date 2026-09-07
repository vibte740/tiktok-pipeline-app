#!/usr/bin/env python3
"""End-to-end smoke test: mocks 9Router, hits every major API endpoint, renders one episode."""
import json, os, subprocess, sys, time, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 9876
NINEROUTER_PORT = 9877
NINEROUTER_URL = f"http://127.0.0.1:{NINEROUTER_PORT}/v1"
API = f"http://127.0.0.1:{PORT}"

def run(cmd, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def req(path, data=None, method="GET"):
    url = API + path
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read())
    except Exception as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"  HTTP ERROR: {body[:500]}")
        return None

def main():
    print("=== Starting mock 9Router ===")
    mock = subprocess.Popen(
        [sys.executable, os.path.join(BASE, "tests", "mock_tts.py"), str(NINEROUTER_PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    time.sleep(1)
    if mock.poll() is not None:
        print("FATAL: mock 9Router failed to start"); sys.exit(1)

    print("=== Starting server ===")
    os.environ["NINEROUTER_URL"] = NINEROUTER_URL
    os.environ["VIBTE_WORKROOT"] = os.path.join(BASE, "work")
    os.environ["VIBTE_HERMES_DIR"] = os.path.join(BASE, "work", "hermes")
    os.chdir(BASE)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    print("  waiting for server...")
    time.sleep(3)
    if server.poll() is not None:
        out = server.stdout.read()
        print("FATAL: server died:\n" + out[-2000:]); sys.exit(1)
    try:
        with urllib.request.urlopen(f"{API}/api/health", timeout=5):
            print("  server healthy")
    except Exception as e:
        print(f"  health check failed: {e}")
        sys.exit(1)

    errs = []

    # --- Episodes ---
    print("\n=== GET /api/episodes ===")
    eps = req("/api/episodes")
    print(f"  {len(eps)} episodes")
    assert len(eps) == 79, f"expected 79 episodes, got {len(eps)}"

    print("\n=== GET /api/levels ===")
    lvl = req("/api/levels")
    print(f"  {lvl}")

    ep0 = eps[0]["id"]
    print(f"\n=== GET /api/episodes/{ep0} ===")
    assert req(f"/api/episodes/{ep0}") is not None

    print("\n=== GET /api/pick ===")
    pick = req("/api/pick")
    print(f"  picked: {pick['id']}")
    assert pick is not None

    # --- State ---
    print("\n=== GET /api/state ===")
    st = req("/api/state")
    print(f"  produced: {len(st['produced'])}, registry: {len(st['registry'])}")

    # --- TikTok status ---
    print("\n=== GET /api/tiktok/status ===")
    tt = req("/api/tiktok/status")
    print(f"  {tt}")

    # --- MEGA status ---
    print("\n=== GET /api/mega/status ===")
    mg = req("/api/mega/status")
    print(f"  {mg}")

    # --- Integrations ---
    print("\n=== GET /api/integrations ===")
    integ = req("/api/integrations")
    print(f"  {integ}")

    # --- Artifacts list ---
    print("\n=== GET /api/artifacts ===")
    arts = req("/api/artifacts")
    print(f"  {len(arts)} artifacts (expected 0)")

    # --- Render one episode (render-only) ---
    print(f"\n=== Render episode: {ep0} (draft) ===")
    rj = req("/api/render", {"episode_id": ep0})
    jid = rj["job_id"]
    print(f"  job_id: {jid}")
    time.sleep(5)
    j = req(f"/api/jobs/{jid}")
    print(f"  status: {j['status']}, logs tail: {j['logs'][-3:]}")
    assert j["status"] == "done", f"render job not done: {j['status']}"
    assert "artifacts" in j["result"]
    vid = j["result"]["artifacts"].get("video")
    print(f"  video link: {vid}")
    if vid:
        r = urllib.request.urlopen(API + vid, timeout=10)
        print(f"  video downloadable: {len(r.read())} bytes")

    print("\n=== GET /api/jobs (after render) ===")
    jlist = req("/api/jobs")
    print(f"  {len(jlist)} jobs")

    print("\n=== Artifacts (after render) ===")
    arts2 = req("/api/artifacts")
    print(f"  {len(arts2)} artifact sets")
    assert len(arts2) >= 1

    # --- Producer (render-only, auto pick) ---
    print("\n=== Producer (render-only, auto pick) ===")
    pj = req("/api/producer", {"render_only": True})
    pjid = pj["job_id"]
    print(f"  job_id: {pjid}")
    time.sleep(6)
    pj2 = req(f"/api/jobs/{pjid}")
    print(f"  status: {pj2['status']}")
    if pj2["result"]:
        print(f"  produced: {pj2['result'].get('episode_id')}")

    print("\n=== DONE ===")
    print("\nAll tests passed.")

    server.terminate()
    mock.terminate()
    server.wait(timeout=5)
    mock.wait(timeout=5)
    sys.exit(0)

if __name__ == "__main__":
    main()