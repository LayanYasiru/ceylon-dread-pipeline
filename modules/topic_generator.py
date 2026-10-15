"""Keeps the story pool full forever: asks Gemini for NEW Sri Lankan horror topics and rejects near-duplicates."""
import difflib
import json
import re

from config import GENERATED_TOPICS_FILE, STORY_COOLDOWN_DAYS
from modules import llm

SYSTEM = """You are a story editor for a Sri Lankan horror storytelling channel (English narration).
Invent NEW topics. Mix: Sinhala/Tamil/Muslim/Burgher/Veddah folklore and village beliefs, urban legends, hill-country plantation tales,
coastal and fishing-village tales, old colonial buildings, railways, roads, schools, hospitals, wells, rivers, jungles, ritual traditions (respectfully).
Spread them across ALL provinces and eras. Each topic must be a clearly different premise, not a re-skin of another one.
Rules: no real private individuals, no real named businesses accused of anything, no mockery of religion; real history only if well known.
Return ONLY JSON."""


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:48]


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", t.lower())}


def _too_similar(new: dict, existing: list[dict]) -> bool:
    nt, ns = new["title"].lower(), _words(new["title"] + " " + new["seed"])
    for e in existing:
        if difflib.SequenceMatcher(None, nt, e["title"].lower()).ratio() > 0.62:
            return True
        es = _words(e["title"] + " " + e.get("seed", ""))
        if ns and es and len(ns & es) / len(ns | es) > 0.42:
            return True
    return False


def generate_topics(existing: list[dict], n: int = 30) -> int:
    """Append up to n accepted new topics to data/generated_topics.json. Returns how many were added."""
    titles = "\n".join("- " + e["title"] for e in existing)
    prompt = f"""Existing topics (do NOT repeat or paraphrase any of these):
{titles}

Create {n} NEW topics as JSON: {{"topics": [{{"title": "...", "kind": "folklore|urban_legend|practice|history|legend_with_history",
"seed": "2-3 sentences: the premise, who tells it, the central unsettling idea, how the story is framed (folklore vs fiction)",
"setting": "specific visual setting for image prompts: place, time of night, weather, props"}}]}}"""
    data = llm.generate_json(prompt, system=SYSTEM, temperature=1.0)
    try:
        current = json.loads(GENERATED_TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        current = []
    pool = list(existing)
    added = 0
    for t in data.get("topics", []):
        if not all(k in t for k in ("title", "kind", "seed", "setting")):
            continue
        t["id"] = _slug(t["title"])
        if not t["id"] or any(t["id"] == e["id"] for e in pool) or _too_similar(t, pool):
            continue
        current.append(t)
        pool.append(t)
        added += 1
    GENERATED_TOPICS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Topics] generated {added} new topics (pool now {len(pool)}; cooldown {STORY_COOLDOWN_DAYS} days)")
    return added
