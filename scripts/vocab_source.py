"""
Vocab Source — Oxford 3000/5000 CEFR-leveled word list + Free Dictionary API.

Chains two free sources to produce episode-ready dicts:
1. Oxford 5000 CSV (word, level, pos) from GitHub
2. Free Dictionary API (definition, example sentences)

Usage:
    from vocab_source import VocabSource
    vs = VocabSource()
    episode = vs.next_episode(exclude_ids=set(), target_level="B2")
"""

import csv
import io
import json
import logging
import os
import random
import time
import requests
from pathlib import Path

# Load .env from this directory
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

log = logging.getLogger("vocab_source")

OXFORD_CSV_URL = os.environ.get("OXFORD_CSV_URL", "https://raw.githubusercontent.com/nalgeon/words/main/data/oxford-5k.csv")
LOCAL_CACHE = Path(os.environ.get("VIBTE_OXFORD_CACHE", str(Path(__file__).parent / "oxford_5k.csv")))
DICT_API_URL = os.environ.get("DICT_API_URL", "https://api.dictionaryapi.dev/api/v2/entries/en/{word}")

LEVEL_MAP = {
    "a1": "LEVEL A1", "a2": "LEVEL A2",
    "b1": "LEVEL B1", "b2": "LEVEL B2",
    "c1": "LEVEL C1", "c2": "LEVEL C2",
}

POS_LABELS = {
    "noun": "Noun",
    "verb": "Verb",
    "adjective": "Adjective",
    "adverb": "Adverb",
    "preposition": "Preposition",
    "conjunction": "Conjunction",
    "pronoun": "Pronoun",
    "interjection": "Interjection",
}


class VocabSource:
    def __init__(self):
        self.words = []
        self._load_oxford()

    def _load_oxford(self):
        """Load Oxford 5000 CSV from cache or download."""
        if LOCAL_CACHE.exists():
            log.info(f"Loading Oxford 5000 from cache: {LOCAL_CACHE}")
            with open(LOCAL_CACHE, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.words = list(reader)
        else:
            log.info(f"Downloading Oxford 5000 from {OXFORD_CSV_URL}")
            resp = requests.get(OXFORD_CSV_URL, timeout=30)
            resp.raise_for_status()
            LOCAL_CACHE.write_text(resp.text, encoding="utf-8")
            reader = csv.DictReader(io.StringIO(resp.text))
            self.words = list(reader)
        log.info(f"Loaded {len(self.words)} Oxford words")

    def _fetch_definition(self, word: str) -> dict | None:
        """Fetch definition + examples from Free Dictionary API."""
        url = DICT_API_URL.format(word=word)
        try:
            resp = requests.get(url, timeout=3, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data or not isinstance(data, list):
                return None
            
            entry = data[0]
            meanings = entry.get("meanings", [])
            if not meanings:
                return None
            
            # Get first meaning with definition + example
            for m in meanings:
                defs = m.get("definitions", [])
                for d in defs:
                    definition = d.get("definition", "")
                    example = d.get("example", "")
                    pos = m.get("partOfSpeech", "")
                    if definition:
                        return {
                            "pos": POS_LABELS.get(pos, pos.title()),
                            "definition": definition,
                            "example": example or f'"{word.capitalize()} is important."',
                        }
        except Exception as e:
            log.warning(f"Dict API failed for '{word}': {e}")
        return None

    def _make_pair(self, word: str, level: str) -> dict | None:
        """Build a word-pair episode dict for a target word."""
        # Use Oxford CSV directly (skip unreliable Free Dictionary API)
        primary = self._fallback_definition(word)
        if not primary:
            return None
        
        # Find a related/confusable word at similar level
        same_level = [w for w in self.words if w.get("level", "").lower() == level.lower() and w["word"] != word]
        if not same_level:
            return None
        
        word2 = random.choice(same_level)["word"]
        secondary = self._fallback_definition(word2)
        if not secondary:
            return None
        
        # Build episode structure matching episodes.json format
        slug = f"{word.lower()}-vs-{word2.lower()}"
        
        return {
            "id": slug,
            "level": LEVEL_MAP.get(level.lower(), f"LEVEL {level.upper()}"),
            "subtitle": "Commonly Confused Words",
            "word1": word.upper(),
            "word1_color": [56, 189, 248],  # blue
            "word2": word2.upper(),
            "word2_color": [255, 130, 58],   # orange
            "word1_pos": primary["pos"],
            "word1_def": primary["definition"],
            "word1_example": primary["example"],
            "word1_icon": self._icon_for_pos(primary["pos"]),
            "word2_pos": secondary["pos"],
            "word2_def": secondary["definition"],
            "word2_example": secondary["example"],
            "word2_icon": self._icon_for_pos(secondary["pos"]),
            "cards_header": f"{word.upper()} vs {word2.upper()}",
            "word1_card_sub": primary["definition"][:30],
            "word1_card_lines": [primary["definition"][:30]],
            "word2_card_sub": secondary["definition"][:30],
            "word2_card_lines": [secondary["definition"][:30]],
            "quiz_question": [f"I need to ___ the right word.", f"Is it {word} or {word2}?"],
            "result_line1": f"{word.upper()} = {primary['definition'][:30]}",
            "result_line2": f"{word2.upper()} = {secondary['definition'][:30]}",
            "promo_tagline": f"{word.title()} vs {word2.title()} — Know the difference.",
            "tts": [
                f"{level} English: {word} versus {word2}.",
                f"{word.capitalize()} means {primary['definition']}. {primary['example']}",
                f"{word2.capitalize()} means {secondary['definition']}. {secondary['example']}",
                f"{word} is about {primary['definition'][:20]}. {word2} is about {secondary['definition'][:20]}. Know the difference.",
                f"Quiz: I need to blank the right word. {word} or {word2}?",
                f"Answer: {word}. {word.capitalize()} means {primary['definition'][:30]}. {word2.capitalize()} means {secondary['definition'][:30]}.",
            ],
        }

    def _fallback_definition(self, word: str) -> dict | None:
        """Fallback: use Oxford CSV definition URL (we just synthesize a basic def)."""
        for w in self.words:
            if w["word"].lower() == word.lower():
                pos = POS_LABELS.get(w.get("pos", "").lower(), w.get("pos", "").title() or "Noun")
                return {
                    "pos": pos,
                    "definition": f"Definition of {word} (from Oxford {w.get('level','').upper()})",
                    "example": f'"{word.capitalize()} example sentence."',
                }
        return None

    def _icon_for_pos(self, pos: str) -> str:
        icons = {
            "Noun": "noun",
            "Verb": "verb",
            "Adjective": "adjective",
            "Adverb": "adverb",
            "Preposition": "preposition",
            "Conjunction": "conjunction",
        }
        return icons.get(pos, "word")

    def next_episode(self, exclude_ids: set[str], target_level: str = "B1") -> dict | None:
        """Get next episode for target level, excluding already-made IDs."""
        level_words = [w for w in self.words 
                       if w.get("level", "").lower() == target_level.lower() 
                       and w["word"] not in exclude_ids]
        
        if not level_words:
            # Fallback: any level
            level_words = [w for w in self.words if w["word"] not in exclude_ids]
        
        if not level_words:
            return None
        
        random.shuffle(level_words)

        for w in level_words[:20]:  # cap tries so a slow/unreachable dict API can't hang forever
            word = w["word"]
            episode = self._make_pair(word, target_level)
            if episode and episode["id"] not in exclude_ids:
                time.sleep(0.2)  # rate limit dict API
                return episode
        
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    vs = VocabSource()
    exclude = set()
    if len(sys.argv) > 1:
        exclude = set(sys.argv[1].split(","))
    
    ep = vs.next_episode(exclude_ids=exclude, target_level="B1")
    if ep:
        print(json.dumps(ep, indent=2))
    else:
        print("No episode generated")