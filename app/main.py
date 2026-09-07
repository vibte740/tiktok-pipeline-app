import json
import os
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field

from . import pipeline, auth, wizard
from .jobs import JobManager

app = FastAPI(title="Vibte TikTok Pipeline", version="1.0.0")
jobs = JobManager()
LOGIN_HTML = Path(__file__).parent / "static" / "login.html"
INDEX_HTML = Path(__file__).parent / "static" / "index.html"
PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/check", "/healthz"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)
        user = auth.get_session_user(request)
        if user:
            request.state.user = user
            return await call_next(request)
        if path.startswith("/api"):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

app.add_middleware(AuthMiddleware)
auth.init_default_user()

class LoginReq(BaseModel):
    username: str
    password: str
class WizardSubject(BaseModel):
    subject: str
class WizardContent(BaseModel):
    draft_id: str
    content: dict
class WizardRender(BaseModel):
    draft_id: str
class WizardUpload(BaseModel):
    draft_id: str
class MegaConfig(BaseModel):
    email: str = ""
    password: str = ""
    access_url: str = ""
    base: str = "/Root/tiktok-english/Production"

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN_HTML.read_text()

@app.post("/api/auth/login")
def api_login(body: LoginReq):
    if not auth.authenticate(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return auth.login_response(body.username, "/")

@app.get("/api/auth/logout")
def api_logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        auth.logout_session(token)
    return auth.logout_response()

@app.get("/api/auth/check")
def api_auth_check(request: Request):
    user = auth.get_session_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {"authenticated": True, "username": user}

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML.read_text()

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/api/health")
def health():
    return {"ok": True, "ninerouter": os.environ.get("NINEROUTER_URL")}

@app.get("/api/episodes")
def api_episodes():
    return pipeline.episodes_with_status()

@app.get("/api/episodes/{ep_id}")
def api_episode(ep_id: str):
    ep = pipeline.find_episode(ep_id)
    if not ep:
        raise HTTPException(404, "episode not found")
    return ep

@app.get("/api/artifacts")
def api_artifacts_list():
    out=[]
    for slug_dir in sorted(pipeline.ARTIFACTS.iterdir()):
        if not slug_dir.is_dir(): continue
        files = sorted(p.name for p in slug_dir.iterdir())
        out.append({"slug": slug_dir.name, "files": files})
    return out

@app.get("/api/artifacts/{slug}/{filename}")
def api_artifact(slug: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    target = pipeline.ARTIFACTS / slug / filename
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(target)

@app.post("/api/render")
def api_render(req: dict):
    ep = None
    if req.get("episode_id"):
        ep = pipeline.find_episode(req["episode_id"])
    if req.get("episode"):
        ep = req["episode"]
        ep.setdefault("id", req["episode"].get("id") or req.get("episode_id") or "custom")
    if not ep:
        raise HTTPException(400, "episode_id or episode required")
    if ep["id"] in pipeline.produced_set():
        raise HTTPException(409, f"episode '{ep['id']}' already exists in database (vibte_videos) — already produced")
    jid = jobs.submit(f"render:{ep['id']}", pipeline.render_job(ep))
    return {"job_id": jid}

@app.get("/api/jobs")
def api_jobs():
    return [j.to_dict() for j in jobs.list()]

@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j.to_dict()

@app.get("/api/jobs/{jid}/log")
def api_job_log(jid: str, after: int = 0):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return {"logs": j.logs[after:], "offset": len(j.logs), "status": j.status}

@app.get("/api/mega/status")
def api_mega_status():
    return pipeline.mega_status()

@app.get("/api/mega/config")
def api_mega_config():
    env = pipeline._load_env()
    return {"email": env.get("MEGA_EMAIL",""), "email_set": bool(env.get("MEGA_EMAIL")), "password_set": bool(env.get("MEGA_PASSWORD")), "access_url": env.get("MEGA_ACCESS_URL",""), "access_url_set": bool(env.get("MEGA_ACCESS_URL")), "base": env.get("MEGA_BASE","/Root/tiktok-english/Production")}

@app.put("/api/mega/config")
def api_mega_config_save(body: MegaConfig):
    env = pipeline._load_env()
    for k in ("MEGA_EMAIL","MEGA_PASSWORD","MEGA_ACCESS_URL"): env.pop(k, None)
    if body.email: env["MEGA_EMAIL"]=body.email
    if body.password: env["MEGA_PASSWORD"]=body.password
    if body.access_url: env["MEGA_ACCESS_URL"]=body.access_url
    env["MEGA_BASE"]=body.base
    lines=[f"{k}={v}" for k,v in env.items() if k.startswith("MEGA_")]
    pipeline.HERMES.mkdir(parents=True, exist_ok=True)
    pipeline.TIKTOK_ENV.write_text("\n".join(lines)+"\n")
    os.chmod(pipeline.TIKTOK_ENV, 0o600)
    return {"ok": True}

@app.get("/api/mega/list")
def api_mega_list():
    return pipeline.mega_list()

@app.post("/api/mega/upload")
async def api_mega_upload(file: UploadFile = File(...), remote_path: str | None = None):
    data = await file.read()
    local = pipeline.UPLOADS / file.filename
    local.write_bytes(data)
    remote = remote_path or f'{pipeline._load_env().get("MEGA_BASE","/Root/tiktok-english/Production")}/{file.filename}'
    jid = jobs.submit(f"mega:{file.filename}", pipeline.mega_upload_job(str(local), remote))
    return {"job_id": jid, "local": str(local), "remote_path": remote}

@app.post("/api/vocab/generate")
def api_vocab_generate(body: dict):
    try:
        ep = pipeline.vocab_generate(body.get("level","B1"), body.get("exclude_ids",[]))
    except Exception as e:
        raise HTTPException(502, f"vocab generation failed: {e}")
    if not ep:
        raise HTTPException(404, "no episode could be generated")
    return ep

@app.post("/api/ai/generate")
def api_ai_generate(body: dict):
    if not body.get("subject","").strip():
        raise HTTPException(400, "subject is required")
    try:
        ep = pipeline.ai_generate_only(body["subject"].strip(), body.get("level"))
    except Exception as e:
        raise HTTPException(502, f"AI generation failed: {e}")
    return ep

@app.get("/api/wizard/drafts")
def wizard_list_drafts():
    return wizard.WizardDraft.list_all()

@app.post("/api/wizard/new")
def wizard_new_draft():
    draft = wizard.WizardDraft.create_new()
    return {"draft_id": draft.draft_id, "step": draft.current_step}

@app.get("/api/wizard/{draft_id}")
def wizard_load_draft(draft_id: str):
    draft = wizard.WizardDraft.load(draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    return {"draft_id": draft.draft_id, "created_at": draft.created_at, "updated_at": draft.updated_at, "subject": draft.subject.__dict__, "content": draft.content.__dict__, "video": draft.video.__dict__, "mega": draft.mega.__dict__, "current_step": draft.current_step}

@app.get("/api/database/check/{episode_id}")
def api_database_check(episode_id: str):
    produced = pipeline.produced_set()
    return {"episode_id": episode_id, "exists": episode_id in produced, "produced_count": len(produced), "all_produced": sorted(produced)}

@app.get("/api/database/status")
def api_database_status():
    import urllib.request, json as jlib
    url = os.environ.get("SUPABASE_URL") or pipeline._load_env().get("SUPABASE_URL","")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or pipeline._load_env().get("SUPABASE_SERVICE_KEY","")
    produced = pipeline.produced_set()
    eps = pipeline.load_episodes()
    remaining = [e["id"] for e in eps if e["id"] not in produced]
    return {"supabase_url": url, "supabase_configured": bool(url and key and "your-project" not in url), "produced_count": len(produced), "total_episodes": len(eps), "remaining_count": len(remaining), "produced": sorted(produced), "remaining_sample": remaining[:10]}

@app.post("/api/wizard/step1")
def wizard_step1(body: WizardSubject):
    try:
        normalized = wizard.normalize_subject(body.subject)
    except ValueError as e:
        raise HTTPException(400, str(e))
    draft = wizard.WizardDraft.create_new()
    draft.subject.subject = body.subject
    draft.subject.slug = normalized["slug"]
    draft.subject.topic_a = normalized["topic_a"]
    draft.subject.topic_b = normalized["topic_b"]
    draft.subject.level = normalized["level"]
    draft.subject.subtitle = normalized["subtitle"]
    draft.current_step = 2
    try:
        content = wizard.generate_content_from_ai(normalized["slug"], normalized["topic_a"], normalized["topic_b"], normalized["level"], normalized["subtitle"])
        draft.content = content
    except Exception as e:
        draft.content = wizard.EpisodeContent(word1=normalized["topic_a"], word1_pos="Noun", word1_def="See definition", word1_example='"Example."', word2=normalized["topic_b"], word2_pos="Noun", word2_def="See definition", word2_example='"Example."', cards_header=f"{normalized['topic_a']} vs {normalized['topic_b']}", word1_card_sub="About "+normalized["topic_a"], word1_card_lines=["Point 1","Point 2"], word2_card_sub="About "+normalized["topic_b"], word2_card_lines=["Point 1","Point 2"], quiz_question=["Fill in the blank: ___?","Choose the correct word."], result_line1=f"{normalized['topic_a']} = see above", result_line2=f"{normalized['topic_b']} = see above", promo_tagline=f"{normalized['topic_a']} vs {normalized['topic_b']} — Know the difference.", tts=[f"{normalized['level']} English: {normalized['topic_a'].lower()} versus {normalized['topic_b'].lower()}.", f"{normalized['topic_a']} — definition. Example.", f"{normalized['topic_b']} — definition. Example.", f"{normalized['topic_a']} is about one thing, {normalized['topic_b']} is about another.", "Quiz: Which one fits?", f"Answer: {normalized['topic_a']}. Know the difference."])
    draft.save()
    return {"draft_id": draft.draft_id, "normalized": normalized, "content": draft.content.__dict__, "step": 2}

@app.post("/api/wizard/step2/save")
def wizard_step2_save(body: WizardContent):
    draft = wizard.WizardDraft.load(body.draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    content = wizard.EpisodeContent(**body.content)
    errors = wizard.validate_content(content)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    draft.content = content
    draft.current_step = 3
    draft.save()
    return {"draft_id": draft.draft_id, "step": 3, "valid": True}

class WizardDraftId(BaseModel):
    draft_id: str

@app.post("/api/wizard/step2/generate")
def wizard_step2_generate(body: WizardDraftId):
    draft = wizard.WizardDraft.load(body.draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    try:
        content = wizard.generate_content_from_ai(draft.subject.slug, draft.subject.topic_a, draft.subject.topic_b, draft.subject.level, draft.subject.subtitle)
    except Exception as e:
        raise HTTPException(502, f"AI generation failed: {e}")
    draft.content = content
    draft.save()
    return {"draft_id": draft.draft_id, "content": content.__dict__}

class WizardTtsPreview(BaseModel):
    draft_id: str
    index: int = 0

@app.post("/api/wizard/step2/tts-preview")
def wizard_step2_tts_preview(body: WizardTtsPreview):
    draft = wizard.WizardDraft.load(body.draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    if body.index < 0 or body.index >= 6:
        raise HTTPException(400, "index must be 0-5")
    text = draft.content.tts[body.index] if draft.content.tts and len(draft.content.tts) > body.index else ""
    if not text or not text.strip():
        tts_input = body.model_extra.get("text") if hasattr(body,"model_extra") and body.model_extra else None
        if not tts_input:
            raise HTTPException(400, "TTS line is empty")
        text = tts_input
    import urllib.request, json as jsonlib
    env = pipeline._load_env()
    NINEROUTER = env.get("NINEROUTER_URL") or os.environ.get("NINEROUTER_URL","http://127.0.0.1:20128/v1")
    API_KEY = env.get("NINEROUTER_API_KEY") or env.get("NINEROUTER_TOKEN") or os.environ.get("NINEROUTER_API_KEY","")
    payload = jsonlib.dumps({"model": "edge-tts/en-US-GuyNeural", "input": text}).encode()
    req = urllib.request.Request(f"{NINEROUTER.rstrip('/')}/audio/speech", data=payload, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio_data = resp.read()
    except Exception as e:
        raise HTTPException(502, f"TTS generation failed: {e}")
    return Response(content=audio_data, media_type="audio/mpeg")

@app.post("/api/wizard/step3/render")
def wizard_step3_render(body: WizardRender):
    draft = wizard.WizardDraft.load(body.draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    errors = wizard.validate_content(draft.content)
    if errors:
        raise HTTPException(400, "content invalid: " + "; ".join(errors))
    if draft.subject.slug in pipeline.produced_set():
        raise HTTPException(409, f"episode '{draft.subject.slug}' already exists in database — already produced")
    if wizard.is_duplicate_slug(draft.subject.slug):
        raise HTTPException(409, f"episode '{draft.subject.slug}' already exists in episodes.json or database")
    episode = wizard.build_episode_dict(draft)
    jid = jobs.submit(f"wizard-render:{draft.subject.slug}", pipeline.render_job(episode, draft_id=body.draft_id))
    return {"job_id": jid, "draft_id": body.draft_id}

class WizardPoll(BaseModel):
    job_id: str

@app.post("/api/wizard/step3/poll")
def wizard_step3_poll(body: WizardPoll):
    job = jobs.get(body.job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status == "done" and job.result:
        draft = wizard.WizardDraft.load(job.result.get("draft_id",""))
        if draft and job.result.get("artifacts",{}).get("video"):
            draft.video = wizard.VideoArtifact(local_path=job.result["artifacts"]["video"], duration_s=job.result.get("duration",0), size_mb=job.result.get("size_mb",0))
            draft.current_step = 4
            if not draft.mega.timestamp:
                draft.mega.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            draft.save()
    return job.to_dict()

@app.post("/api/wizard/step4/upload")
def wizard_step4_upload(body: WizardUpload):
    draft = wizard.WizardDraft.load(body.draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    if not draft.video.local_path:
        raise HTTPException(400, "no video to upload — render first")
    local_path = draft.video.local_path
    if local_path.startswith("/api/artifacts/"):
        parts = local_path.split("/")
        if len(parts) >= 4:
            slug = parts[3]; filename = parts[4]
            local_path = str(pipeline.ARTIFACTS / slug / filename)
    if not os.path.exists(local_path):
        raise HTTPException(404, "video file not found on disk")
    if not draft.mega.timestamp:
        draft.mega.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        draft.save()
    timestamp = draft.mega.timestamp
    remote_name = f"{draft.subject.slug}_{timestamp}.mp4"
    base = pipeline._load_env().get("MEGA_BASE") or os.environ.get("MEGA_BASE","/Root/tiktok-english/Production")
    remote_path = f"{base.rstrip('/')}/{remote_name}"
    env = pipeline.child_env()
    env.update(pipeline._load_env())
    rc, out = pipeline.run_cmd(["python3", str(pipeline.MEGA_SCRIPT), "upload", local_path, remote_path], lambda x: None, env=env)
    if rc != 0:
        raise HTTPException(500, f"MEGA upload failed: {out[-800:]}")
    link_line = next((l for l in out.splitlines() if l.startswith("LINK=")), "")
    link = link_line.split("=",1)[1] if link_line else remote_path
    draft.mega = wizard.MegaReceipt(mega_path=remote_path, mega_link=link, timestamp=timestamp)
    draft.current_step = 5
    draft.save()
    return {"draft_id": draft.draft_id, "mega_path": remote_path, "mega_link": link, "done": True}

@app.get("/api/wizard/{draft_id}/video")
def wizard_get_video(draft_id: str):
    draft = wizard.WizardDraft.load(draft_id)
    if not draft or not draft.video.local_path:
        raise HTTPException(404, "no video")
    local_path = draft.video.local_path
    if local_path.startswith("/api/artifacts/"):
        parts = local_path.split("/")
        if len(parts) >= 4:
            slug = parts[3]; filename = parts[4]
            local_path = str(pipeline.ARTIFACTS / slug / filename)
    if not os.path.exists(local_path):
        raise HTTPException(404, "video file not found")
    return FileResponse(local_path, media_type="video/mp4")

@app.get("/api/integrations")
def api_integrations():
    return {"supabase_url": os.environ.get("SUPABASE_URL",""), "supabase_table": os.environ.get("SUPABASE_TABLE","vibte_videos"), "supabase_key_set": bool(os.environ.get("SUPABASE_SERVICE_KEY")), "ninerouter_url": os.environ.get("NINEROUTER_URL"), "workroot": str(pipeline.HERMES.parent)}
