"""Real quality gates (replaces the old rubber-stamp evaluator)."""
import json
import re
from pathlib import Path

from config import DATA_DIR, TARGET_WORDS

SCRIPTS_DIR = DATA_DIR / "scripts"
SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# Claims a horror channel must not invent. Folklore must be framed as folklore.
FABRICATED_CLAIM_PATTERNS = [
    r"verified (?:colonial )?(?:archives|records)",
    r"scientists (?:have )?(?:confirmed|proved|proven)",
    r"(?:officially|scientifically) (?:confirmed|documented|proven)",
    r"police (?:records|files|archives) (?:show|confirm|prove)",
    r"15 degrees",
    r"subscribe and hit the bell",
    r"don't forget to like",
]
CLICHES = ["bone-deep", "primal", "materialist skeptic", "optical silhouette", "chilling anomaly", "little did he know"]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _ngrams(words: list[str], n: int = 4) -> set:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def full_text(script: dict) -> str:
    return " ".join(s["narration"] for s in script.get("scenes", []))


def check_script(script: dict, story_id: str) -> list[str]:
    """Return a list of problems (empty list = pass)."""
    problems = []
    scenes = script.get("scenes", [])
    text = full_text(script)
    n_words = len(_words(text))

    if not (0.8 * TARGET_WORDS <= n_words <= 1.3 * TARGET_WORDS):
        problems.append(f"Narration is {n_words} words; it must be between {int(0.8 * TARGET_WORDS)} and {int(1.3 * TARGET_WORDS)}.")
    if not (8 <= len(scenes) <= 14):
        problems.append(f"Need 8-14 scenes, got {len(scenes)}.")
    for i, s in enumerate(scenes, 1):
        if len(s.get("visuals", [])) < 2:
            problems.append(f"Scene {i} needs 2 distinct visual prompts.")
        if len(_words(s.get("narration", ""))) < 40:
            problems.append(f"Scene {i} narration is too short (<40 words).")
    if not script.get("title") or len(script["title"]) > 90:
        problems.append("Title missing or longer than 90 characters.")
    if len(script.get("shorts", [])) < 1:
        problems.append("At least one short is required.")
    for j, sh in enumerate(script.get("shorts", []), 1):
        w = len(_words(sh.get("narration", "")))
        if not (85 <= w <= 150):
            problems.append(f"Short {j} narration is {w} words; must be 85-150.")
        if len(sh.get("visuals", [])) < 4:
            problems.append(f"Short {j} needs at least 4 visual prompts.")

    lowered = text.lower()
    for pat in FABRICATED_CLAIM_PATTERNS:
        if re.search(pat, lowered):
            problems.append(f"Contains a fabricated-authority / filler phrase matching /{pat}/. Reframe as folklore or remove.")
    for c in CLICHES:
        if c in lowered:
            problems.append(f"Cliche to remove: '{c}'.")

    # Repetition against previously written scripts
    mine = _ngrams(_words(text))
    for f in SCRIPTS_DIR.glob("*.json"):
        if f.stem == story_id:
            continue
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        theirs = _ngrams(_words(full_text(old)))
        if mine and theirs:
            jacc = len(mine & theirs) / len(mine | theirs)
            if jacc > 0.06:
                problems.append(f"Too similar to the earlier script '{f.stem}' (overlap {jacc:.0%}). Use different wording and structure.")
        first_old = " ".join(_words(full_text(old))[:8])
        first_new = " ".join(_words(text)[:8])
        if first_old == first_new:
            problems.append(f"Opening line duplicates '{f.stem}'.")
    return problems


def save_script(story_id: str, script: dict) -> Path:
    p = SCRIPTS_DIR / f"{story_id}.json"
    p.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def check_image(path: Path) -> bool:
    """Reject missing, tiny, or nearly blank/solid images."""
    try:
        from PIL import Image, ImageStat
        if not path.exists() or path.stat().st_size < 15000:
            return False
        with Image.open(path) as im:
            im = im.convert("L").resize((64, 64))
            return ImageStat.Stat(im).stddev[0] > 6
    except Exception:
        return False
