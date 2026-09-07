import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(os.environ.get("VIBTE_SCRIPTS_DIR", BASE_DIR / "scripts"))
sys.path.insert(0, str(SCRIPTS_DIR))
WORKROOT = Path(os.environ.get("VIBTE_WORKROOT", BASE_DIR / "work"))
ARTIFACTS = WORKROOT / "artifacts"
HERMES = Path(os.environ.get("VIBTE_HERMES_DIR", WORKROOT / "hermes"))
UPLOADS = WORKROOT / "uploads"
for d in (WORKROOT, ARTIFACTS, HERMES, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)

EPISODES_FILE = Path(os.environ.get("VIBTE_EPISODES_FILE", SCRIPTS_DIR / "episodes.json"))
GEN_SCRIPT = SCRIPTS_DIR / "generate_vibte_video.py"
MEGA_SCRIPT = SCRIPTS_DIR / "mega_upload.py"
TIKTOK_ENV = HERMES / ".env"

os.environ.setdefault("VIBTE_HERMES_DIR", str(HERMES))
os.environ.setdefault("VIBTE_EPISODES_FILE", str(EPISODES_FILE))
os.environ.setdefault("NINEROUTER_URL", "http://127.0.0.1:20128/v1")
os.environ.setdefault("NINEROUTER_API_KEY", "")

def child_env(registry=None, extra=None):
    env = dict(os.environ)
    env["VIBTE_HERMES_DIR"] = str(HERMES)
    env["VIBTE_EPISODES_FILE"] = str(EPISODES_FILE)
    env.setdefault("NINEROUTER_URL", "http://127.0.0.1:20128/v1")
    env.setdefault("NINEROUTER_API_KEY", os.environ.get("NINEROUTER_API_KEY", ""))
    _hermes_env = _load_env()
    for k in ("NINEROUTER_URL","NINEROUTER_API_KEY","MEGA_EMAIL","MEGA_PASSWORD","MEGA_ACCESS_URL","MEGA_BASE","AI_MODEL","NINEROUTER_TOKEN"):
        if k in _hermes_env and k not in env:
            env[k] = _hermes_env[k]
    if registry:
        env["VIBTE_REGISTRY"] = str(registry)
    if extra:
        env.update(extra)
    return env

def load_episodes():
    with open(EPISODES_FILE) as f:
        return json.load(f)

def find_episode(ep_id):
    for ep in load_episodes():
        if ep["id"] == ep_id:
            return ep
    return None

def run_cmd(cmd, log, env=None, cwd=None, timeout=3600):
    log(f"$ {' '.join(str(c) for c in cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env, cwd=cwd)
    lines=[]
    for line in proc.stdout:
        line=line.rstrip("\n"); log(line); lines.append(line)
    try:
        rc=proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); rc=-9; log("[timeout] killed after %ss"%timeout)
    log(f"[exit {rc}]")
    return rc, "\n".join(lines)

def store_artifacts(slug, video_path, episode, log):
    dest = ARTIFACTS / slug
    dest.mkdir(parents=True, exist_ok=True)
    saved={}
    if video_path and os.path.exists(video_path):
        target = dest / "video.mp4"
        shutil.copy2(video_path, target)
        saved["video"]="video.mp4"
        saved["video_size_mb"]=round(os.path.getsize(target)/1e6,1)
        try:
            r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(target)], capture_output=True, text=True, timeout=10)
            if r.returncode==0 and r.stdout.strip():
                saved["duration_s"]=round(float(r.stdout.strip()),1)
        except Exception:
            pass
    with open(dest/"episode.json","w") as f:
        json.dump(episode,f,indent=2)
    saved["episode"]="episode.json"
    log(f"[web] artifacts stored: {dest}")
    return dest, saved

def artifacts_links(slug, saved):
    return {k: f"/api/artifacts/{slug}/{v}" for k,v in saved.items() if k in ("video","episode")}

def render_job(episode, registry=None, draft_id=None):
    def _run(log):
        ep_id=episode["id"]; slug=ep_id.replace("_","-")
        import vibte_producer as prod
        prod.TEMPLATE=str(GEN_SCRIPT)
        script_path=f"/tmp/vibte_{slug}.py"
        prod.generate_script(episode, script_path)
        rc,_=run_cmd(["python3", script_path], log, env=child_env(registry=registry))
        if rc!=0:
            raise RuntimeError(f"render failed (rc={rc})")
        video=f"/tmp/vibte_{slug}.mp4"
        if not os.path.exists(video):
            raise RuntimeError("render produced no video")
        dest, saved = store_artifacts(slug, video, episode, log)
        for p in (script_path, video):
            try:
                if p and os.path.exists(p): os.remove(p)
            except Exception: pass
        shutil.rmtree(f"/tmp/vibte_{slug}_pipeline", ignore_errors=True)
        result={"episode_id":ep_id,"slug":slug,"artifacts":artifacts_links(slug,saved),"duration":saved.get("duration_s",0),"size_mb":saved.get("video_size_mb",0)}
        if draft_id: result["draft_id"]=draft_id
        return result
    return _run

def producer_job(episode_id=None, render_only=False, no_upload=False):
    def _run(log):
        cmd=["python3", str(SCRIPTS_DIR/"vibte_producer.py"), "--keep-artifacts"]
        if episode_id: cmd+=["--episode",episode_id]
        if render_only: cmd+=["--render-only"]
        if no_upload: cmd+=["--no-upload"]
        rc,text=run_cmd(cmd, log, env=child_env())
        res={}
        for key in ("RESULT_EPISODE","RESULT_VIDEO"):
            m=re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
            if m: res[key]=m.group(1).strip()
        ep_id=res.get("RESULT_EPISODE"); slug=(ep_id or "").replace("_","-")
        result={"episode_id":ep_id,"slug":slug,"artifacts":{}}
        if ep_id:
            episode=find_episode(ep_id) or {"id":ep_id}
            dest,saved=store_artifacts(slug, res.get("RESULT_VIDEO"), episode, log)
            result["artifacts"]=artifacts_links(slug,saved)
        if rc!=0: raise RuntimeError(f"producer failed (rc={rc})")
        return result
    return _run

def mega_upload_python(src_path, remote_path, log=None):
    env=child_env(); env.update(_load_env())
    rc,out=run_cmd(["python3", str(MEGA_SCRIPT), "upload", src_path, remote_path], log or (lambda x: None), env=env)
    if rc!=0: raise RuntimeError("MEGA upload failed")
    link_line=next((l for l in out.splitlines() if l.startswith("LINK=")), "")
    return link_line.split("=",1)[1] if link_line else remote_path

def ai_generate_only(subject, level_hint=None):
    import app.ai as ai
    return ai.generate_episode(subject, level_hint)

def ai_render_job(subject, level_hint=None, upload=True):
    import app.ai as ai
    def _run(log):
        episode=ai.generate_episode(subject, level_hint)
        ep_id=episode["id"]; slug=ep_id.replace("_","-")
        import vibte_producer as prod
        prod.TEMPLATE=str(GEN_SCRIPT)
        script_path=f"/tmp/vibte_{slug}.py"
        prod.generate_script(episode, script_path)
        rc,_=run_cmd(["python3", script_path], log, env=child_env())
        if rc!=0: raise RuntimeError(f"render failed (rc={rc})")
        video=f"/tmp/vibte_{slug}.mp4"
        dest,saved=store_artifacts(slug, video, episode, log)
        result={"episode_id":ep_id,"slug":slug,"artifacts":artifacts_links(slug,saved),"episode":episode}
        if upload and os.path.exists(video):
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            remote=f'{os.environ.get("MEGA_BASE","/Root/tiktok-english/Production")}/{slug}_{ts}.mp4'
            link=mega_upload_python(video, remote, log)
            result["mega"]={"path":remote,"link":link}
        for p in (script_path, video):
            try: os.remove(p)
            except: pass
        shutil.rmtree(f"/tmp/vibte_{slug}_pipeline", ignore_errors=True)
        return result
    return _run

def mega_commands():
    if shutil.which("megaput"): return {"put":"megaput","ls":"megals","whoami":"megawho","put_args":["--path"],"ls_export":["-e"]}
    if shutil.which("mega-put"): return {"put":"mega-put","ls":"mega-ls","whoami":"mega-whoami","put_args":["--path"],"ls_export":["--export"]}
    return None

def mega_python_env():
    env=_load_env()
    return env.get("MEGA_EMAIL") or os.environ.get("MEGA_EMAIL"), env.get("MEGA_PASSWORD") or os.environ.get("MEGA_PASSWORD"), env.get("MEGA_ACCESS_URL") or os.environ.get("MEGA_ACCESS_URL")

def _load_env():
    e=dict(os.environ)
    if TIKTOK_ENV.exists():
        for line in TIKTOK_ENV.read_text().splitlines():
            line=line.strip()
            if line and not line.startswith("#") and "=" in line:
                k,v=line.split("=",1)
                e.setdefault(k.strip(), v.strip().strip("'\""))
    return e

def mega_available():
    cmds=mega_commands()
    if cmds: return cmds.get("put")
    email,password,access_url=mega_python_env()
    if (email and password) or access_url: return "python-mega"
    return None

def mega_status():
    cmds=mega_commands()
    method=cmds.get("put") if cmds else None
    python_avail=False
    try:
        email,password,access_url=mega_python_env()
        python_avail= (bool(email) and bool(password)) or bool(access_url)
    except: pass
    base=os.environ.get("MEGA_BASE","/Root/tiktok-english/Production")
    herm=_load_env()
    if herm.get("MEGA_BASE"): base=herm["MEGA_BASE"]
    return {"enabled": bool(method) or python_avail, "cli":method, "python": python_avail if not method else False, "base":base, "logged_in": bool(mega_python_env()[0])}

def mega_list(log=None):
    base=os.environ.get("MEGA_BASE","/Root/tiktok-english/Production")
    hm=_load_env()
    if hm.get("MEGA_BASE"): base=hm["MEGA_BASE"]
    cmds=mega_commands()
    if cmds:
        try:
            r=subprocess.run([cmds["ls"],"-l",base], capture_output=True, text=True, timeout=30)
            return {"base":base,"rc":r.returncode,"raw":r.stdout.strip(),"error":r.stderr.strip(),"cli":cmds["put"]}
        except Exception as e:
            return {"base":base,"rc":None,"raw":"","error":str(e),"cli":cmds["put"]}
    return {"base":base,"rc":None,"raw":"","error":"python backend (list not implemented)","cli":None}

def mega_upload_job(src_path, remote_path):
    def _run(log):
        cmds=mega_commands()
        if cmds:
            rc,_=run_cmd([cmds["put"]]+cmds["put_args"]+[remote_path, src_path], log, env=child_env())
            if rc!=0: raise RuntimeError("MEGA CLI upload failed")
            link=remote_path
            try:
                lscmd="megals" if cmds["put"]=="megaput" else "mega-ls"
                r=subprocess.run([lscmd]+cmds["ls_export"]+[remote_path], capture_output=True, text=True, timeout=30)
                if r.stdout.strip(): link=r.stdout.strip().splitlines()[0]
            except: pass
            log(f"link: {link}")
            return {"remote_path":remote_path,"link":link}
        else:
            env=child_env(); env.update(_load_env())
            rc,out=run_cmd(["python3", str(MEGA_SCRIPT), "upload", src_path, remote_path], log, env=env)
            if rc!=0: raise RuntimeError("MEGA python upload failed")
            link_line=next((l for l in out.splitlines() if l.startswith("LINK=")), "")
            link=link_line.split("=",1)[1] if link_line else remote_path
            log(f"link: {link}")
            return {"remote_path":remote_path,"link":link}
    return _run

def vocab_generate(target_level="B1", exclude_ids=None):
    from vocab_source import VocabSource
    vs=VocabSource()
    return vs.next_episode(exclude_ids=set(exclude_ids or []), target_level=target_level)

def _supabase_ids():
    url = os.environ.get("SUPABASE_URL") or _load_env().get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or _load_env().get("SUPABASE_SERVICE_KEY")
    if not url or not key or "your-project" in url:
        return set()
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/vibte_videos?select=episode_id", headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return {r["episode_id"] for r in data if r.get("episode_id")}
    except Exception as e:
        print(f"[warn] supabase ids fetch failed: {e}", flush=True)
        return set()

def produced_set():
    return _supabase_ids()

def episodes_with_status():
    eps = load_episodes()
    produced = _supabase_ids()
    return [{"id": ep["id"], "level": ep.get("level"), "subtitle": ep.get("subtitle"), "word1": ep.get("word1"), "word2": ep.get("word2"), "produced": ep["id"] in produced} for ep in eps]

def pick_next():
    import vibte_producer as prod
    episodes = load_episodes()
    return prod.pick_next(episodes, _supabase_ids())

def tiktok_status():
    return {"client_key_set": False, "has_token": False, "note": "removed per spec — scope is MEGA only"}
def tiktok_auth_url():
    raise RuntimeError("TikTok removed per spec")
def tiktok_publish_job(*a, **kw):
    raise RuntimeError("TikTok removed per spec")

def episodes_with_status_legacy():
    return episodes_with_status()
def read_registry(path):
    try:
        with open(path) as f: data=json.load(f); return data if isinstance(data,list) else [data]
    except: return []
PROD_REGISTRY=HERMES/"episodes_registry.json"
DRAFT_REGISTRY=HERMES/"drafts_registry.json"
STATE_FILE=HERMES/"vibte_producer_state.json"
TITLES_FILE=HERMES/"vibte_video_titles.txt"
TIKTOK_TOKEN=HERMES/"tiktok_token.json"
REGISTRY_DIR=HERMES
WORKROOT=HERMES.parent if str(HERMES).endswith("hermes") else WORKROOT
