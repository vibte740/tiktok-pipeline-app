#!/usr/bin/env python3
"""
Vibte Producer — picks next unproduced episode, generates standalone script
from template, renders video, uploads to MEGA Production.
NO REPEATS: uses Supabase table vibte_videos to track created videos.
"""
import json, os, sys, subprocess, shutil, glob, re, random
from datetime import datetime
from typing import Optional

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "generate_vibte_video.py")
EPISODES_FILE = os.environ.get("VIBTE_EPISODES_FILE", os.path.join(BASE, "episodes.json"))
HERMES_DIR = os.environ.get("VIBTE_HERMES_DIR", os.path.join(os.path.expanduser("~"), ".hermes"))
STATE_FILE = os.environ.get("VIBTE_STATE_FILE", os.path.join(HERMES_DIR, "vibte_producer_state.json"))
MEGA_BASE = os.environ.get("MEGA_BASE", "/Root/tiktok-english/Production")

# Supabase config for vibte.com
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://psupntfqbnyawrzugaeu.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SUPABASE_TABLE = "vibte_videos"

# Level distribution weights (user-approved): B1 20%, B2 40%, C1 30%, C2 10%
LEVEL_WEIGHTS = {
    "LEVEL B1": 20,
    "LEVEL B2": 40,
    "LEVEL C1": 30,
    "LEVEL C2": 10,
}

os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

# ── Supabase helpers ──
def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

def load_completed_from_supabase() -> set:
    """Fetch all episode IDs already recorded in Supabase."""
    if not SUPABASE_SERVICE_KEY or SUPABASE_URL == "https://your-project.supabase.co":
        print("[warn] Supabase not configured — falling back to local ledger", flush=True)
        return load_titles_local()
    
    try:
        import urllib.request
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?select=episode_id"
        req = urllib.request.Request(url, headers=supabase_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return {row["episode_id"] for row in data}
    except Exception as e:
        print(f"[warn] Supabase query failed: {e} — falling back to local", flush=True)
        return load_titles_local()

def save_to_supabase(episode_id: str, slug: str, mega_link: str, tiktok_status: Optional[str] = None):
    """Insert a new record into vibte_videos table."""
    if not SUPABASE_SERVICE_KEY or SUPABASE_URL == "https://your-project.supabase.co":
        print("[warn] Supabase not configured — saving to local ledger only", flush=True)
        save_title_local(episode_id)
        return
    
    try:
        import urllib.request
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        payload = {
            "episode_id": episode_id,
            "slug": slug,
            "mega_link": mega_link,
            "tiktok_status": tiktok_status or "pending",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=supabase_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                print(f"[info] Recorded to Supabase: {episode_id}", flush=True)
            else:
                print(f"[warn] Supabase insert returned {resp.status}", flush=True)
    except Exception as e:
        print(f"[warn] Supabase insert failed: {e} — saving to local ledger", flush=True)
        save_title_local(episode_id)

# ── Local fallback (original behavior) ──
TITLES_FILE = os.environ.get("VIBTE_TITLES_FILE", os.path.join(HERMES_DIR, "vibte_video_titles.txt"))

def load_titles_local() -> set:
    if not os.path.exists(TITLES_FILE):
        return set()
    with open(TITLES_FILE) as f:
        return {line.strip() for line in f if line.strip()}

def save_title_local(title: str):
    with open(TITLES_FILE, "a") as f:
        f.write(f"{title}\n")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except: pass
    return {"completed": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def pick_next(episodes, made_titles):
    """Weighted random pick by level (B1 20%, B2 40%, C1 30%, C2 10%).
    Skips ANY episode whose title is already in the ledger (no repeats ever)."""
    remaining = [ep for ep in episodes if ep["id"] not in made_titles]
    if not remaining:
        return None

    # Group remaining by level
    by_level = {}
    for ep in remaining:
        lvl = ep.get("level", "LEVEL B1")
        by_level.setdefault(lvl, []).append(ep)

    # Weighted choice among levels that still have episodes
    levels = [lvl for lvl in LEVEL_WEIGHTS if by_level.get(lvl)]
    weights = [LEVEL_WEIGHTS[lvl] for lvl in levels]
    if not levels:
        return remaining[0]  # all weighted levels done — fallback

    chosen_level = random.choices(levels, weights=weights, k=1)[0]
    pool = by_level[chosen_level]
    return random.choice(pool)

def generate_script(episode, output_path):
    """Read template, inject EPISODE dict + paths, write standalone script."""
    with open(TEMPLATE) as f:
        template = f.read()

    ep_id = episode["id"]
    slug = ep_id.replace("_", "-")

    # Build EPISODE dict string
    ep_dict_lines = ["EPISODE = {"]
    ep_dict_lines.append(f'    "level": {json.dumps(episode["level"])},')
    ep_dict_lines.append(f'    "subtitle": {json.dumps(episode["subtitle"])},')
    ep_dict_lines.append(f'    "word1": {json.dumps(episode["word1"])},')
    ep_dict_lines.append(f'    "word1_color": {json.dumps(episode["word1_color"])},')
    ep_dict_lines.append(f'    "word2": {json.dumps(episode["word2"])},')
    ep_dict_lines.append(f'    "word2_color": {json.dumps(episode["word2_color"])},')
    ep_dict_lines.append(f'    "word1_pos": {json.dumps(episode["word1_pos"])},')
    ep_dict_lines.append(f'    "word1_def": {json.dumps(episode["word1_def"])},')
    ep_dict_lines.append(f'    "word1_example": {json.dumps(episode["word1_example"])},')
    ep_dict_lines.append(f'    "word1_icon": {json.dumps(episode["word1_icon"])},')
    ep_dict_lines.append(f'    "word2_pos": {json.dumps(episode["word2_pos"])},')
    ep_dict_lines.append(f'    "word2_def": {json.dumps(episode["word2_def"])},')
    ep_dict_lines.append(f'    "word2_example": {json.dumps(episode["word2_example"])},')
    ep_dict_lines.append(f'    "word2_icon": {json.dumps(episode["word2_icon"])},')
    ep_dict_lines.append(f'    "cards_header": {json.dumps(episode["cards_header"])},')
    ep_dict_lines.append(f'    "word1_card_sub": {json.dumps(episode["word1_card_sub"])},')
    ep_dict_lines.append(f'    "word1_card_lines": {json.dumps(episode["word1_card_lines"])},')
    ep_dict_lines.append(f'    "word2_card_sub": {json.dumps(episode["word2_card_sub"])},')
    ep_dict_lines.append(f'    "word2_card_lines": {json.dumps(episode["word2_card_lines"])},')
    ep_dict_lines.append(f'    "quiz_title": "Quick Quiz",')
    ep_dict_lines.append(f'    "quiz_question": {json.dumps(episode["quiz_question"])},')
    ep_dict_lines.append(f'    "quiz_prompt": "Which one fits?",')
    ep_dict_lines.append(f'    "result_line1": {json.dumps(episode["result_line1"])},')
    ep_dict_lines.append(f'    "result_line2": {json.dumps(episode["result_line2"])},')
    ep_dict_lines.append(f'    "promo_link": "vibte.com",')
    ep_dict_lines.append(f'    "promo_tagline": {json.dumps(episode["promo_tagline"])},')
    ep_dict_lines.append(f'    "tts": {json.dumps(episode["tts"])},')
    ep_dict_lines.append(f'    "episode_id_slug": {json.dumps(slug)},')
    ep_dict_lines.append(f'    "word_pair": [{json.dumps(episode["word1"].lower())}, {json.dumps(episode["word2"].lower())}],')
    ep_dict_lines.append("}")
    ep_dict_str = "\n".join(ep_dict_lines)

    # Replace OUT_ROOT and OUT_VIDEO
    template = re.sub(
        r'OUT_ROOT = .*',
        f'OUT_ROOT = "/tmp/vibte_{slug}_pipeline"',
        template
    )
    template = re.sub(
        r'OUT_VIDEO = .*',
        f'OUT_VIDEO = "/tmp/vibte_{slug}.mp4"',
        template
    )

    # Replace EPISODE block
    ep_start = template.find("EPISODE = {")
    if ep_start == -1:
        raise ValueError("Cannot find EPISODE = { in template")
    depth = 0
    ep_end = ep_start
    for i in range(ep_start, len(template)):
        if template[i] == '{':
            depth += 1
        elif template[i] == '}':
            depth -= 1
            if depth == 0:
                ep_end = i + 1
                break

    new_template = template[:ep_start] + ep_dict_str + template[ep_end:]

    with open(output_path, "w") as f:
        f.write(new_template)

    return output_path

def run_script(script_path):
    result = subprocess.run(["python3", script_path], capture_output=True, text=True, timeout=600)
    print(result.stdout)
    if result.stderr:
        print(result.stderr[-2000:], file=sys.stderr)
    return result.returncode

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Vibte producer")
    ap.add_argument("--episode", default=None, help="Force a specific episode id (renders it even if already produced)")
    ap.add_argument("--render-only", action="store_true", help="Render only: skip MEGA/TikTok uploads AND skip ledger/state marking")
    ap.add_argument("--keep-artifacts", action="store_true", help="Do not delete /tmp artifacts after run")
    ap.add_argument("--no-upload", action="store_true", help="Skip MEGA upload (render + mark done locally)")
    args = ap.parse_args()

    with open(EPISODES_FILE) as f:
        episodes = json.load(f)

    # Load completed from Supabase (with local fallback)
    made_titles = load_completed_from_supabase()
    state = load_state()

    if args.episode:
        episode = next((ep for ep in episodes if ep["id"] == args.episode), None)
        if not episode:
            print(f"❌ Episode not found: {args.episode}", flush=True)
            sys.exit(1)
    else:
        episode = pick_next(episodes, made_titles)

    if not episode:
        print("✅ All episodes completed! Nothing to produce.")
        return

    ep_id = episode["id"]
    slug = ep_id.replace("_", "-")
    script_path = f"/tmp/vibte_{slug}.py"

    print(f"🎬 Generating: {ep_id} ({episode['level']})", flush=True)

    # Generate script
    try:
        generate_script(episode, script_path)
    except Exception as e:
        print(f"❌ Script generation failed: {e}", flush=True)
        sys.exit(1)

    # Run script
    print(f"📽️  Running: {script_path}", flush=True)
    rc = run_script(script_path)

    if rc != 0:
        print(f"❌ {ep_id} failed (exit {rc})", flush=True)
        sys.exit(1)

    video_path = f"/tmp/vibte_{slug}.mp4"
    pipeline_dir = f"/tmp/vibte_{slug}_pipeline"
    contact_path = os.path.join(pipeline_dir, "contact_sheet", "contact_sheet.jpg")

    uploaded = False
    mega_link = None

    if not args.render_only:
        # Upload to MEGA
        if os.path.exists(video_path) and not args.no_upload:
            remote_name = f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            remote_path = f"{MEGA_BASE}/{remote_name}"
            print("📤 Uploading to MEGA...", flush=True)
            mega_script = os.path.join(BASE, "mega_upload.py")
            r = subprocess.run(
                ["python3", mega_script, "upload", video_path, remote_path],
                capture_output=True, text=True, timeout=600,
            )
            link = None
            if r.returncode == 0:
                for line in (r.stdout or "").splitlines():
                    if line.startswith("LINK="):
                        link = line.split("=", 1)[1]
                if link is None:
                    link = remote_path
                print(f"  🔗 {link}", flush=True)
                uploaded = True
                mega_link = link
            else:
                print(f"  ⚠️  MEGA upload: {(r.stderr or r.stdout)[-400:]}", flush=True)
            print(f"RESULT_MEGA_PATH={remote_path}", flush=True)
            print(f"RESULT_MEGA_LINK={mega_link or ''}", flush=True)
        elif os.path.exists(video_path):
            uploaded = True  # --no-upload still counts as done
        else:
            print(f"  ⚠️  Video not found at {video_path}", flush=True)

        # TikTok auto-publish (after MEGA success — video still on disk)
        tiktok_result = None
        if uploaded and os.path.exists(video_path):
            import subprocess as _sp
            tt = _sp.run(
                ["python3", os.path.join(BASE, "tiktok_uploader.py"), video_path, ep_id],
                capture_output=True, text=True, timeout=600,
            )
            tt_out = (tt.stdout or "") + (tt.stderr or "")
            print(f"  🎬 TikTok: {tt_out.strip()[-400:]}", flush=True)
            if tt.returncode == 2:
                print("  ⚠️  TikTok needs one-time authorization (see tiktok_uploader.py --help)", flush=True)
            elif tt.returncode == 0 and "publish_id" in tt_out:
                tiktok_result = "published"
                print("  ✅ TikTok published", flush=True)
            else:
                print(f"  ⚠️  TikTok upload skipped/failed (exit {tt.returncode})", flush=True)

        # Only mark as complete when the job is done (not in render-only mode)
        if uploaded:
            state["completed"].append(ep_id)
            save_state(state)
            save_to_supabase(ep_id, slug, mega_link, tiktok_result)
            tt_note = " + TikTok" if tiktok_result else ""
            print(f"✅ {ep_id} done!{tt_note} ({len(set(state['completed']))}/{len(episodes)} produced)", flush=True)
        else:
            print(f"⚠️ {ep_id} rendered but NOT uploaded — will retry next run", flush=True)
    else:
        print(f"🔧 render-only: {ep_id} rendered, NOT marked complete, not uploaded", flush=True)

    print(f"RESULT_EPISODE={ep_id}", flush=True)
    print(f"RESULT_VIDEO={video_path}", flush=True)
    print(f"RESULT_CONTACT={contact_path}", flush=True)
    print(f"RESULT_MEGA_PATH={mega_link or ''}", flush=True)
    print(f"RESULT_MEGA_LINK={mega_link or ''}", flush=True)  # mega_link holds share link when uploaded

    # Cleanup (always remove video files — server keeps no videos)
    if not args.keep_artifacts:
        for p in [script_path, video_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except: pass
        try:
            shutil.rmtree(pipeline_dir, ignore_errors=True)
        except: pass

if __name__ == "__main__":
    main()
