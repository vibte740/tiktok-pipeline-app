import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

HERMES_DIR = Path(os.environ.get("VIBTE_HERMES_DIR", os.path.expanduser("~") + "/.hermes"))
DRAFTS_DIR = HERMES_DIR / "wizard_drafts"
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
EPISODES_FILE = Path(os.environ.get("VIBTE_EPISODES_FILE", "/app/scripts/episodes.json"))

def load_episodes():
    with open(EPISODES_FILE) as f:
        return json.load(f)

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'[^a-z0-9_\-]', '', text)
    text = re.sub(r'_+', '_', text)
    return text.strip('_-')

def infer_level(topic_a: str, topic_b: str) -> str:
    text = f"{topic_a} {topic_b}".lower()
    common_b1 = {"job","work","accept","except","already","ready","affect","effect","their","there","then","than","to","too","its","it","your","youre","lose","loose","advice","advise","weather","whether","desert","dessert","principle","principal"}
    words = set(re.findall(r"[a-z]+", text))
    if words & common_b1:
        return "LEVEL B1"
    if len(text) < 12:
        return "LEVEL B1"
    if any(w in text for w in ["advanced","nuance","subtle","formal","ambiguous","connotation","etymology","archaeology"]):
        return "LEVEL C1"
    if len(words) >= 2 and max(len(w) for w in words) >= 8:
        return "LEVEL C1"
    if max(len(w) for w in words) >= 6:
        return "LEVEL B2"
    return "LEVEL B2"

def infer_subtitle(topic_a: str, topic_b: str) -> str:
    return "Commonly Confused Words"

def is_duplicate_slug(slug: str) -> bool:
    eps = load_episodes()
    ids = {e["id"] for e in eps}
    if slug in ids:
        return True
    alt = slug.replace("_", "-")
    if alt in ids:
        return True
    alt2 = slug.replace("-", "_")
    if alt2 in ids:
        return True
    try:
        import urllib.request, json as _j, os as _os
        from pathlib import Path as _P
        herm = _P(_os.environ.get("VIBTE_HERMES_DIR", _os.path.expanduser("~") + "/.hermes")) / ".env"
        env = dict(_os.environ)
        if herm.exists():
            for line in herm.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip("'\""))
        url = env.get("SUPABASE_URL", "")
        key = env.get("SUPABASE_SERVICE_KEY", "")
        if url and key and "your-project" not in url:
            req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/vibte_videos?select=episode_id&episode_id=eq.{slug}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _j.loads(resp.read().decode())
                if data:
                    return True
            for a in (alt, alt2):
                req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/vibte_videos?select=episode_id&episode_id=eq.{a}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if _j.loads(resp.read().decode()):
                        return True
    except Exception:
        pass
    return False

@dataclass
class SubjectDraft:
    subject: str = ""
    slug: str = ""
    topic_a: str = ""
    topic_b: str = ""
    level: str = "LEVEL B1"
    subtitle: str = "Commonly Confused Words"
    step: int = 1

@dataclass
class EpisodeContent:
    word1: str = ""
    word1_color: list = field(default_factory=lambda: [56, 189, 248])
    word1_pos: str = ""
    word1_def: str = ""
    word1_example: str = ""
    word1_icon: str = "checkmark"
    word2: str = ""
    word2_color: list = field(default_factory=lambda: [255, 130, 58])
    word2_pos: str = ""
    word2_def: str = ""
    word2_example: str = ""
    word2_icon: str = "group"
    cards_header: str = ""
    word1_card_sub: str = ""
    word1_card_lines: list = field(default_factory=list)
    word2_card_sub: str = ""
    word2_card_lines: list = field(default_factory=list)
    quiz_question: list = field(default_factory=lambda: ["", ""])
    quiz_prompt: str = "Which one fits?"
    result_line1: str = ""
    result_line2: str = ""
    promo_link: str = "vibte.com"
    promo_tagline: str = ""
    tts: list = field(default_factory=lambda: ["", "", "", "", "", ""])

@dataclass
class VideoArtifact:
    local_path: str = ""
    duration_s: float = 0.0
    size_mb: float = 0.0
    thumbnail: str = ""

@dataclass
class MegaReceipt:
    mega_path: str = ""
    mega_link: str = ""
    timestamp: str = ""

@dataclass
class WizardDraft:
    draft_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    subject: SubjectDraft = field(default_factory=SubjectDraft)
    content: EpisodeContent = field(default_factory=EpisodeContent)
    video: VideoArtifact = field(default_factory=VideoArtifact)
    mega: MegaReceipt = field(default_factory=MegaReceipt)
    current_step: int = 1

    def save(self):
        self.updated_at = time.time()
        path = DRAFTS_DIR / f"{self.draft_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2))
        return self

    @classmethod
    def load(cls, draft_id: str) -> Optional["WizardDraft"]:
        path = DRAFTS_DIR / f"{draft_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return cls(
            draft_id=data["draft_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            subject=SubjectDraft(**data.get("subject", {})),
            content=EpisodeContent(**data.get("content", {})),
            video=VideoArtifact(**data.get("video", {})),
            mega=MegaReceipt(**data.get("mega", {})),
            current_step=data.get("current_step", 1),
        )

    @classmethod
    def create_new(cls) -> "WizardDraft":
        draft = cls()
        draft.save()
        return draft

    @classmethod
    def list_all(cls) -> list:
        drafts = []
        for path in sorted(DRAFTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text())
                drafts.append({
                    "draft_id": data["draft_id"],
                    "subject": data["subject"].get("subject", ""),
                    "slug": data["subject"].get("slug", ""),
                    "current_step": data.get("current_step", 1),
                    "updated_at": data["updated_at"],
                })
            except Exception:
                pass
        return drafts

def normalize_subject(subject: str) -> dict:
    subject = subject.strip()
    if not subject:
        raise ValueError("Subject is required")
    if len(subject) < 3:
        raise ValueError("Subject must be at least 3 characters")
    low = subject.lower()
    if " vs " not in low and " versus " not in low and " vs. " not in low:
        raise ValueError("Subject must contain 'vs' or two terms separated by 'vs'")
    parts = re.split(r'\s+(?:vs|versus|vs\.)\s+', subject, flags=re.IGNORECASE)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError("Could not parse two terms from subject")
    topic_a, topic_b = parts[0].strip(), parts[1].strip()
    slug = slugify(f"{topic_a}_vs_{topic_b}")
    if is_duplicate_slug(slug):
        raise ValueError(f"Episode '{slug}' already exists")
    level = infer_level(topic_a, topic_b)
    subtitle = infer_subtitle(topic_a, topic_b)
    return {"slug": slug, "topic_a": topic_a.upper(), "topic_b": topic_b.upper(), "level": level, "subtitle": subtitle}

def generate_content_from_ai(slug: str, topic_a: str, topic_b: str, level: str, subtitle: str) -> EpisodeContent:
    import app.ai as ai
    subject_text = f"{topic_a.lower()} vs {topic_b.lower()}"
    ep = ai.generate_episode(subject_text, level.replace("LEVEL ", ""))
    return EpisodeContent(
        word1=ep.get("word1", topic_a.upper()),
        word1_color=ep.get("word1_color", [56, 189, 248]),
        word1_pos=ep.get("word1_pos", "Noun"),
        word1_def=ep.get("word1_def", ""),
        word1_example=ep.get("word1_example", ""),
        word1_icon=ep.get("word1_icon", "checkmark"),
        word2=ep.get("word2", topic_b.upper()),
        word2_color=ep.get("word2_color", [255, 130, 58]),
        word2_pos=ep.get("word2_pos", "Noun"),
        word2_def=ep.get("word2_def", ""),
        word2_example=ep.get("word2_example", ""),
        word2_icon=ep.get("word2_icon", "group"),
        cards_header=ep.get("cards_header", f"{topic_a.upper()} vs {topic_b.upper()}"),
        word1_card_sub=ep.get("word1_card_sub", ""),
        word1_card_lines=ep.get("word1_card_lines", []),
        word2_card_sub=ep.get("word2_card_sub", ""),
        word2_card_lines=ep.get("word2_card_lines", []),
        quiz_question=ep.get("quiz_question", ["", ""]),
        quiz_prompt=ep.get("quiz_prompt", "Which one fits?"),
        result_line1=ep.get("result_line1", ""),
        result_line2=ep.get("result_line2", ""),
        promo_link=ep.get("promo_link", "vibte.com"),
        promo_tagline=ep.get("promo_tagline", f"{topic_a.upper()} vs {topic_b.upper()} — Know the difference."),
        tts=ep.get("tts", ["", "", "", "", "", ""]),
    )

def validate_content(content: EpisodeContent) -> list:
    errors = []
    if not content.word1: errors.append("word1 is required")
    if not content.word1_def: errors.append("word1 definition is required")
    if not content.word1_example: errors.append("word1 example is required")
    if not content.word2: errors.append("word2 is required")
    if not content.word2_def: errors.append("word2 definition is required")
    if not content.word2_example: errors.append("word2 example is required")
    if not content.cards_header: errors.append("cards_header is required")
    if not content.quiz_question or len(content.quiz_question) != 2: errors.append("quiz_question must have exactly 2 lines")
    else:
        if not content.quiz_question[0]: errors.append("quiz_question line 1 required")
        if not content.quiz_question[1]: errors.append("quiz_question line 2 required")
    if not content.result_line1: errors.append("result_line1 is required")
    if not content.result_line2: errors.append("result_line2 is required")
    if not content.promo_tagline: errors.append("promo_tagline is required")
    if not content.tts or len(content.tts) != 6: errors.append("tts must have exactly 6 lines")
    else:
        for i, line in enumerate(content.tts):
            if not line: errors.append(f"tts[{i+1}] is empty")
            elif len(line) > 280: errors.append(f"tts[{i+1}] exceeds 280 characters")
    return errors

def build_episode_dict(draft: WizardDraft) -> dict:
    return {
        "id": draft.subject.slug,
        "level": draft.subject.level,
        "subtitle": draft.subject.subtitle,
        "word1": draft.content.word1,
        "word1_color": draft.content.word1_color,
        "word2": draft.content.word2,
        "word2_color": draft.content.word2_color,
        "word1_pos": draft.content.word1_pos,
        "word1_def": draft.content.word1_def,
        "word1_example": draft.content.word1_example,
        "word1_icon": draft.content.word1_icon,
        "word2_pos": draft.content.word2_pos,
        "word2_def": draft.content.word2_def,
        "word2_example": draft.content.word2_example,
        "word2_icon": draft.content.word2_icon,
        "cards_header": draft.content.cards_header,
        "word1_card_sub": draft.content.word1_card_sub,
        "word1_card_lines": draft.content.word1_card_lines,
        "word2_card_sub": draft.content.word2_card_sub,
        "word2_card_lines": draft.content.word2_card_lines,
        "quiz_title": "Quick Quiz",
        "quiz_question": draft.content.quiz_question,
        "quiz_prompt": draft.content.quiz_prompt,
        "result_line1": draft.content.result_line1,
        "result_line2": draft.content.result_line2,
        "promo_link": draft.content.promo_link,
        "promo_tagline": draft.content.promo_tagline,
        "tts": draft.content.tts,
        "episode_id_slug": draft.subject.slug,
        "word_pair": [draft.content.word1.lower(), draft.content.word2.lower()],
    }
