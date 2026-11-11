"""Finding place names in text (caption, speech transcript, on-screen text).

Three layers, each optional and all free:
  1. A local LLM through Ollama, if you have it running. Best results by far.
  2. spaCy named-entity recognition, if installed.
  3. Simple patterns travel creators use: "📍 Place", "Location: ...", numbered lists.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import requests

from .db import normalise

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    name: str
    method: str            # caption | transcript | ocr | llm
    evidence: str
    confidence: float
    category: str | None = None
    area: str | None = None    # city / region / country hint for geocoding
    kind: str = "poi"          # poi (a specific spot) or area (a city / region / country)
    extra: dict = field(default_factory=dict)


# Words that look like places to a naive parser but aren't.
STOPWORDS = {
    "instagram", "reels", "reel", "follow", "save", "share", "link in bio", "bio", "comment", "dm",
    "subscribe", "like", "tiktok", "youtube", "google maps", "maps", "travel", "travel tips", "day",
    "day 1", "day 2", "day 3", "itinerary", "guide", "tips", "budget", "the", "here", "this", "price",
    "prices", "cost", "hours", "open", "closed", "address", "location", "where", "when", "how",
}

PIN_PATTERNS = [
    re.compile(r"(?:📍|🗺️?|🏨|🍽️?|☕️?|🏖️?|🏛️?|⛰️?|🌊|📌)\s*[:\-]?\s*(?P<name>[^\n|•#@]{2,80})"),
    re.compile(r"(?im)^\s*(?:location|where|address|place|spot|hotel|restaurant|cafe|café|stay)\s*[:\-–]\s*"
               r"(?P<name>[^\n|•#@]{2,80})"),
]
NUMBERED = re.compile(
    r"(?m)^\s*(?:\d{1,2}[.)\]]|\d️⃣|[①-⑳]|[-•▪︎▫️✔️✅])\s*(?P<name>[A-Z0-9][^\n:–—\-|(#@]{2,60})"
)


def clean_name(raw: str) -> str:
    name = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", " ", raw)
    name = re.sub(r"\s+", " ", name).strip(" .,:;-–—!*_\"'")
    # "Cafe Name, Lisbon" stays together, but trailing sentences go.
    name = re.split(r"(?<=[a-z])\.\s|\s[-–—|]\s|\s{2,}", name)[0]
    return name.strip()[:80]


def plausible(name: str) -> bool:
    n = normalise(name)
    if len(n) < 3 or n in STOPWORDS or n.isdigit():
        return False
    if len(n.split()) > 8:
        return False
    if re.fullmatch(r"[\d\s$€£%.,]+", name):
        return False
    return any(c.isalpha() for c in name)


# --------------------------------------------------------------------------- patterns
def pattern_candidates(text: str, method: str) -> list[Candidate]:
    out = []
    for pattern, conf in [(p, 0.85) for p in PIN_PATTERNS] + [(NUMBERED, 0.55)]:
        for m in pattern.finditer(text):
            name = clean_name(m.group("name"))
            if plausible(name):
                area = None
                if "," in name:  # "Pastéis de Belém, Lisbon"
                    name, area = (s.strip() for s in name.split(",", 1))
                out.append(Candidate(name=name, method=method, evidence=m.group(0).strip(),
                                     confidence=conf, area=area))
    return out


TITLE_LINE = re.compile(r"^(?:[A-Z\u00C0-\u00DE][\w'’&.\-]*|de|da|do|del|la|le|of|the|di|dos|das)"
                        r"(?:\s+(?:[A-Z\u00C0-\u00DE][\w'’&.\-]*|de|da|do|del|la|le|of|the|di|dos|das)){1,5}$")


def overlay_candidates(ocr: str) -> list[Candidate]:
    """Travel reels often flash a bare place name on screen ("Time Out Market").

    These are weak guesses: they're only kept if the geocoder actually finds them.
    """
    out = []
    for line in ocr.splitlines():
        name = clean_name(line)
        if TITLE_LINE.match(name) and plausible(name):
            out.append(Candidate(name=name, method="ocr", evidence=line.strip(), confidence=0.35))
    return out


# --------------------------------------------------------------------------- spaCy
@functools.lru_cache(maxsize=1)
def _spacy():
    try:
        import spacy
    except ImportError:
        return None
    for model in ("en_core_web_trf", "en_core_web_md", "en_core_web_sm", "xx_ent_wiki_sm"):
        try:
            return spacy.load(model)
        except OSError:
            continue
    log.warning("spaCy installed but no model; run: python -m spacy download en_core_web_sm")
    return None


def ner_candidates(text: str, method: str) -> list[Candidate]:
    nlp = _spacy()
    if nlp is None or not text.strip():
        return []
    out = []
    for ent in nlp(text[:20000]).ents:
        if ent.label_ not in ("GPE", "LOC", "FAC", "ORG"):
            continue
        name = clean_name(ent.text)
        if not plausible(name):
            continue
        kind = "area" if ent.label_ in ("GPE", "LOC") else "poi"
        conf = {"FAC": 0.6, "ORG": 0.4, "LOC": 0.45, "GPE": 0.4}[ent.label_]
        sent = ent.sent.text.strip() if ent.sent is not None else ent.text
        out.append(Candidate(name=name, method=method, evidence=sent[:300], confidence=conf, kind=kind))
    return out


# --------------------------------------------------------------------------- Ollama
LLM_PROMPT = """You extract travel locations from an Instagram post. Below is the caption, \
the spoken audio transcript and the text shown on screen. List every specific place a traveller \
could visit or look up on a map: restaurants, cafes, bars, hotels, beaches, viewpoints, hikes, \
museums, shops, neighbourhoods, towns. Do not include vague things ("a hidden beach") or brands \
that aren't a place. Also give the city/region/country each place is in when you can tell.

Return JSON only: {{"places": [{{"name": "...", "area": "city, country", "category": \
"restaurant|cafe|bar|hotel|beach|viewpoint|hike|museum|attraction|shop|neighbourhood|town|other", \
"source": "caption|speech|screen", "quote": "short exact quote it came from"}}]}}

Account: @{username}
Instagram location tag: {tag}

CAPTION:
{caption}

SPEECH:
{transcript}

ON SCREEN:
{ocr}
"""


@functools.lru_cache(maxsize=4)
def ollama_available(url: str, model: str) -> bool:
    try:
        resp = requests.get(f"{url}/api/tags", timeout=2)
        names = {m.get("name", "") for m in resp.json().get("models", [])}
        base = model.split(":")[0]
        return any(n == model or n.split(":")[0] == base for n in names)
    except Exception:
        return False


def llm_candidates(*, url: str, model: str, caption: str, transcript: str, ocr: str,
                   username: str | None, tag: str | None) -> list[Candidate] | None:
    """None means the LLM wasn't available (so fall back), [] means it found nothing."""
    if not ollama_available(url, model):
        return None
    prompt = LLM_PROMPT.format(
        username=username or "?", tag=tag or "none", caption=caption[:4000] or "(none)",
        transcript=transcript[:6000] or "(none)", ocr=ocr[:4000] or "(none)",
    )
    try:
        resp = requests.post(f"{url}/api/generate", timeout=300, json={
            "model": model, "prompt": prompt, "format": "json", "stream": False,
            "options": {"temperature": 0},
        })
        data = json.loads(resp.json().get("response") or "{}")
    except Exception as exc:
        log.warning("Ollama call failed, falling back to rules: %s", exc)
        return None
    source_map = {"caption": "caption", "speech": "transcript", "screen": "ocr"}
    out = []
    for p in data.get("places", []) if isinstance(data, dict) else []:
        name = clean_name(str(p.get("name") or ""))
        if not plausible(name):
            continue
        category = (p.get("category") or "other").lower()
        out.append(Candidate(
            name=name, method="llm", confidence=0.8, category=category,
            area=(p.get("area") or None), kind="area" if category in ("town",) else "poi",
            evidence=f"[{source_map.get(p.get('source'), 'text')}] {p.get('quote') or ''}".strip(),
        ))
    return out


# --------------------------------------------------------------------------- combine
def guess_area(candidates: list[Candidate], ig_tag: dict | None) -> str | None:
    """The most-mentioned city/country in the post, used to disambiguate place names."""
    if ig_tag and ig_tag.get("city"):
        return ig_tag["city"]
    areas = Counter(c.name for c in candidates if c.kind == "area")
    areas.update(c.area for c in candidates if c.area)
    if ig_tag and ig_tag.get("name"):
        areas[ig_tag["name"]] += 1
    return areas.most_common(1)[0][0] if areas else None


def merge(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate per name, keeping the most confident; mentions in several sources boost it."""
    best: dict[str, Candidate] = {}
    hits: Counter = Counter()
    for c in candidates:
        key = normalise(c.name).replace(" ", "")  # OCR often drops spaces: "deBelem"
        hits[key] += 1
        if key not in best or c.confidence > best[key].confidence:
            if key in best:
                c.area = c.area or best[key].area
                c.category = c.category or best[key].category
            best[key] = c
    for key, c in best.items():
        if hits[key] > 1:
            c.confidence = min(0.95, c.confidence + 0.1 * (hits[key] - 1))
    return list(best.values())


def find_places(*, caption: str, transcript: str, ocr: str, username: str | None,
                ig_tag: dict | None, ollama_url: str, ollama_model: str) -> list[Candidate]:
    llm = llm_candidates(url=ollama_url, model=ollama_model, caption=caption, transcript=transcript,
                         ocr=ocr, username=username, tag=(ig_tag or {}).get("name"))
    candidates: list[Candidate] = list(llm or [])
    # Rules always run: they're cheap and catch the explicit "📍" lines an LLM sometimes drops.
    for text, method in ((caption, "caption"), (transcript, "transcript"), (ocr, "ocr")):
        if not text:
            continue
        candidates += pattern_candidates(text, method)
        if llm is None:
            candidates += ner_candidates(text, method)
            if method == "ocr":
                candidates += overlay_candidates(text)

    area = guess_area(candidates, ig_tag)
    for c in candidates:
        c.area = c.area or (area if normalise(area or "") != normalise(c.name) else None)

    username_n = normalise(username or "")
    return [c for c in merge(candidates) if normalise(c.name) != username_n]
