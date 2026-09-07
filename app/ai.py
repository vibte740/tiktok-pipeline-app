import json
import os
import re
import urllib.request
from pathlib import Path

HERMES_DIR = Path(os.environ.get("VIBTE_HERMES_DIR", os.path.expanduser("~") + "/.hermes"))

def _env():
    env = dict(os.environ)
    env_file = HERMES_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip("'\""))
    return env

_ENV = _env()
NINEROUTER = _ENV.get("NINEROUTER_URL", _ENV.get("NINEROUTER", "http://127.0.0.1:20128/v1"))
API_KEY = _ENV.get("NINEROUTER_API_KEY", _ENV.get("NINEROUTER_TOKEN", ""))
MODEL = _ENV.get("AI_MODEL", "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast")

SYSTEM_PROMPT = """You are an expert English-teaching content producer for Vibte TikTok videos.
Create a "commonly confused words" episode comparing two similar English words,
perfectly matching the exact JSON schema below. All content must be simple, accurate,
and suitable for an ESL learner. Keep TTS lines short and natural (each under 12 words).

Return JSON ONLY. No prose, no markdown fences. Exactly this schema:

{
  "id": "word1-vs-word2",
  "level": "LEVEL B1",   // choose B1, B2, C1, or C2 based on difficulty
  "subtitle": "Commonly Confused Words",
  "word1": "WORD1", "word1_color": [56, 189, 248],
  "word2": "WORD2", "word2_color": [255, 130, 58],
  "word1_pos": "Noun", "word1_def": "short definition", "word1_example": "\\"Example sentence.\\"", "word1_icon": "inbox",
  "word2_pos": "Noun", "word2_def": "short definition", "word2_example": "\\"Example sentence.\\"", "word2_icon": "exclude",
  "cards_header": "WORD1 vs WORD2",
  "word1_card_sub": "short tag", "word1_card_lines": ["bulleted", "points"],
  "word2_card_sub": "short tag", "word2_card_lines": ["bulleted", "points"],
  "quiz_title": "Quick Quiz",
  "quiz_question": ["Fill in the blank line one", "line two of question"],
  "quiz_prompt": "Which one fits?",
  "result_line1": "WORD1 = summary",
  "result_line2": "WORD2 = summary",
  "promo_link": "vibte.com",
  "promo_tagline": "Word1 vs Word2 — Know the difference.",
  "tts": [
    "<level> English: word1 versus word2.",
    "word1 means ... Example sentence.",
    "word2 means ... Example sentence.",
    "Word1 is about ... Word2 is about ....",
    "Quiz: <the quiz question read aloud with blank>",
    "Answer: <word>. <short explanation>."
  ],
  "episode_id_slug": "word1-vs-word2",
  "word_pair": ["word1", "word2"]
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("AI returned no JSON object")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    return obj


def _cfg():
    env = _env()
    url = env.get("NINEROUTER_URL", env.get("NINEROUTER", "http://127.0.0.1:20128/v1")).rstrip("/") + "/v1"
    if not url.endswith("/v1"):
        base = url.rstrip("/")
        if base.endswith("/v1"):
            url = base
        else:
            url = base + "/v1"
    key = env.get("NINEROUTER_API_KEY", env.get("NINEROUTER_TOKEN", ""))
    model = env.get("AI_MODEL", "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    return url, key, model


def generate_episode(subject: str, level_hint: str | None = None) -> dict:
    """Ask the LLM to produce a full episode dict for the given subject."""
    url, api_key, model = _cfg()
    if not api_key:
        raise RuntimeError("NINEROUTER_API_KEY not configured — AI generation unavailable")
    user_prompt = f"Subject: {subject}"
    if level_hint:
        user_prompt += f". Target CEFR level: {level_hint}."
    user_prompt += (
        " Pick the two most commonly confused English words related to this subject"
        " and build the episode JSON now."
    )

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2500,
        "temperature": 0.4,
    }).encode()

    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
    # 9Router may append SSE "data: [DONE]" directly or on its own line.
    raw = re.sub(r"\ndata:\s*\[DONE\]\s*$", "", raw).strip()
    raw = re.sub(r"data:\s*\[DONE\]\s*$", "", raw).strip()
    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    ep = _extract_json(content)

    # Normalize to the fields the renderer / producer expect.
    ep.setdefault("episode_id_slug", ep["id"].replace("_", "-"))
    ep.setdefault("word_pair", [ep["word1"].lower(), ep["word2"].lower()])
    ep.setdefault("quiz_title", "Quick Quiz")
    ep.setdefault("quiz_prompt", "Which one fits?")
    ep.setdefault("promo_link", "vibte.com")
    for k in ("word1_color", "word2_color"):
        if not isinstance(ep.get(k), list):
            ep[k] = [56, 189, 248] if k == "word1_color" else [255, 130, 58]
    ep["id"] = ep["episode_id_slug"]
    return ep