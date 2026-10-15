"""Story selection with a no-repeat ledger.

Rule: a story may not be produced again within STORY_COOLDOWN_DAYS (default = effectively never; every video is a brand-new story).
Every produced+uploaded story is written to data/state.json ("log" with date). When fewer than a safe buffer of eligible
stories remain, new topics are generated automatically (modules/topic_generator.py) and added to data/generated_topics.json.
"""
import json
import random
from datetime import datetime, timedelta, timezone

from config import ARCHIVE_FILE, GENERATED_TOPICS_FILE, STATE_FILE, STORY_COOLDOWN_DAYS, TOPICS_FILE

BUFFER = 24   # always keep ~12 days of unused topics ready; new ones are generated automatically before the pool runs dry


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_state() -> dict:
    state = _load(STATE_FILE, {})
    state.setdefault("log", [])
    return state


def save_state(state: dict) -> None:
    state.pop("used", None)   # legacy key, replaced by dated log
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def all_stories() -> list[dict]:
    stories = list(_load(TOPICS_FILE, [])) + list(_load(GENERATED_TOPICS_FILE, []))
    # Old archive: only the 1814 Kandy Lake drowning is widely documented. Everything else in it is unverified,
    # so every entry is framed as local tradition ("it is said").
    for a in _load(ARCHIVE_FILE, []):
        stories.append({
            "id": a["id"], "kind": "legend_with_history", "title": a["title_en"],
            "seed": f"Location: {a['location']}. Background (treat as local tradition, not proven fact): "
                    f"{a['historical_anchor']} Atmosphere: {a['primary_vibe']}",
            "setting": f"{a['location']}, " + ", ".join(a.get("key_landmarks", [])[:3]),
        })
    return stories


def last_used() -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for e in load_state()["log"]:
        try:
            d = datetime.fromisoformat(e["date"])
        except Exception:
            continue
        if e["id"] not in out or d > out[e["id"]]:
            out[e["id"]] = d
    return out


def eligible(stories: list[dict] | None = None) -> list[dict]:
    """Stories never used, or last used more than the cooldown ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=STORY_COOLDOWN_DAYS)
    lu = last_used()
    return [s for s in (stories or all_stories()) if s["id"] not in lu or lu[s["id"]] < cutoff]


def pick_next(count: int, preferred_id: str | None = None) -> list[dict]:
    stories = all_stories()
    by_id = {s["id"]: s for s in stories}
    if preferred_id:
        return [by_id[preferred_id]]

    pool = eligible(stories)
    if len(pool) < count + BUFFER:
        try:
            from modules import topic_generator
            topic_generator.generate_topics(stories, n=30)
            stories = all_stories()
            pool = eligible(stories)
        except Exception as e:
            print(f"[StoryBank] could not generate new topics ({str(e)[:120]}); continuing with {len(pool)} eligible stories")
    if len(pool) < count:
        raise RuntimeError(f"Only {len(pool)} stories are outside the {STORY_COOLDOWN_DAYS}-day cooldown; refusing to repeat a story early.")
    random.shuffle(pool)

    chosen: list[dict] = []
    for s in pool:                                      # alternate kinds so two videos in a row feel different
        if chosen and s["kind"] == chosen[-1]["kind"] and any(p["kind"] != s["kind"] for p in pool if p not in chosen):
            continue
        chosen.append(s)
        if len(chosen) == count:
            break
    for s in pool:
        if len(chosen) == count:
            break
        if s not in chosen:
            chosen.append(s)
    return chosen


def mark_done(story_id: str, info: dict) -> None:
    state = load_state()
    state["log"].append({"id": story_id, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), **info})
    save_state(state)
