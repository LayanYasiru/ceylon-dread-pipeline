"""Story selection + persistent state (which stories are done, what was published)."""
import json
import random
from datetime import datetime, timezone

from config import ARCHIVE_FILE, STATE_FILE, TOPICS_FILE


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_state() -> dict:
    state = _load(STATE_FILE, {})
    state.setdefault("used", [])
    state.setdefault("log", [])
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def all_stories() -> list[dict]:
    stories = []
    for t in _load(TOPICS_FILE, []):
        stories.append(t)
    # The old archive: only the Kandy Lake drowning (1814) is widely documented. Everything else
    # in it is unverified, so every entry is treated as legend and framed with "it is said".
    for a in _load(ARCHIVE_FILE, []):
        stories.append({
            "id": a["id"],
            "kind": "legend_with_history",
            "title": a["title_en"],
            "seed": f"Location: {a['location']}. Background (treat as local tradition, not proven fact): "
                    f"{a['historical_anchor']} Atmosphere: {a['primary_vibe']}",
            "setting": f"{a['location']}, " + ", ".join(a.get("key_landmarks", [])[:3]),
        })
    return stories


def pick_next(count: int, preferred_id: str | None = None) -> list[dict]:
    """Pick `count` unused stories, mixing kinds so two consecutive videos differ in flavour."""
    state = load_state()
    stories = all_stories()
    by_id = {s["id"]: s for s in stories}
    if preferred_id:
        return [by_id[preferred_id]]

    pool = [s for s in stories if s["id"] not in state["used"]]
    if len(pool) < count:  # everything used once -> start a fresh cycle
        print("[StoryBank] All stories used once; starting a new cycle (scripts will be rewritten from a new angle).")
        state["used"] = []
        save_state(state)
        pool = stories[:]
    random.shuffle(pool)

    chosen: list[dict] = []
    for s in pool:
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
    if story_id not in state["used"]:
        state["used"].append(story_id)
    state["log"].append({"id": story_id, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), **info})
    save_state(state)
