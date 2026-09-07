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

# Curated list of genuinely confusing word pairs (from episodes.json that exist in Oxford)
# These are actual commonly confused English words that learners struggle with
# Only pairs where BOTH words exist in Oxford 5000
def _build_confusing_pairs(oxford_words: set) -> list:
    """Build confusing pairs list filtered to words that exist in Oxford."""
    all_pairs = [
        ("affect", "effect"),
        ("accept", "except"),
        ("advice", "advise"),
        ("their", "there"),
        ("to", "too"),
        ("then", "than"),
        ("lie", "lay"),
        ("borrow", "lend"),
        ("teach", "learn"),
        ("weather", "whether"),
        ("principal", "principle"),
        ("practice", "practise"),
        ("licence", "license"),
        ("win", "beat"),
        ("come", "go"),
        ("expect", "wait"),
        ("risk", "danger"),
        ("imply", "infer"),
        ("breath", "breathe"),
        ("adapt", "adopt"),
        ("say", "tell"),
        ("speak", "talk"),
        ("hear", "listen"),
        ("job", "work"),
        ("fun", "funny"),
        ("remember", "remind"),
        ("precede", "proceed"),
        ("cost", "price"),
        ("quality", "standard"),
        ("result", "outcome"),
        ("success", "achievement"),
        ("benefit", "advantage"),
        ("decision", "choice"),
        ("plan", "strategy"),
        ("problem", "issue"),
        ("solution", "answer"),
        ("opportunity", "chance"),
        ("skill", "ability"),
        ("experience", "knowledge"),
        ("client", "customer"),
        ("career", "job"),
        ("business", "company"),
        ("meeting", "appointment"),
        ("project", "task"),
        ("goal", "objective"),
    ]
    # Filter to only pairs where both words exist in Oxford
    return [(a, b) for a, b in all_pairs if a in oxford_words and b in oxford_words]

# Will be initialized in __init__ after loading words
CONFUSING_PAIRS = []


def _similar_spelling(word1: str, word2: str, threshold: float = 0.6) -> bool:
    """Check if words have similar spelling (simple heuristic)."""
    w1, w2 = word1.lower(), word2.lower()
    if abs(len(w1) - len(w2)) > 3:
        return False
    # Simple Jaccard similarity on bigrams
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s)-1))
    b1, b2 = bigrams(w1), bigrams(w2)
    if not b1 or not b2:
        return False
    return len(b1 & b2) / len(b1 | b2) >= threshold


class VocabSource:
    def __init__(self):
        self.words = []
        self._load_oxford()
        # Build confusing pairs after loading Oxford words
        global CONFUSING_PAIRS
        oxford_words = {w["word"].lower() for w in self.words}
        CONFUSING_PAIRS = _build_confusing_pairs(oxford_words)
        log.info(f"Built {len(CONFUSING_PAIRS)} confusing pairs from Oxford")

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
        
        # Find a genuinely confusing word for this word
        word2 = self._find_confusing_partner(word, level)
        if not word2:
            return None
        
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

    def _find_confusing_partner(self, word: str, level: str) -> str | None:
        """Find a genuinely confusing word partner for the given word."""
        word_lower = word.lower()
        
        # First, check curated confusing pairs
        for a, b in CONFUSING_PAIRS:
            if word_lower == a:
                # Check if partner exists in our word list at same level
                for w in self.words:
                    if w["word"].lower() == b and w.get("level", "").lower() == level.lower():
                        return b
            elif word_lower == b:
                for w in self.words:
                    if w["word"].lower() == a and w.get("level", "").lower() == level.lower():
                        return a
        
        # Fallback: find words with similar spelling at same level
        same_level = [w for w in self.words 
                      if w.get("level", "").lower() == level.lower() and w["word"] != word]
        
        similar = [w for w in same_level if _similar_spelling(word_lower, w["word"].lower())]
        if similar:
            return random.choice(similar)["word"]
        
        # NO FINAL FALLBACK - return None if no genuinely confusing partner found
        # This prevents random unrelated pairs like "prepared vs though"
        return None

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