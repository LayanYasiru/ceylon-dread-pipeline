"""Writes a real, scene-by-scene narrative with Gemini, then loops with concrete feedback until the quality gate passes."""
import json

from config import TARGET_WORDS, SHORTS_PER_VIDEO, CHANNEL_NAME
from modules import llm, quality

SYSTEM = f"""You are the head writer of "{CHANNEL_NAME}", a Sri Lankan horror storytelling YouTube channel narrated in English.
Your voice: a calm, intimate storyteller sitting beside a lamp at night. Sri Lankan English flavour: the way an uncle tells a story at a wake,
with real local texture (tea, kerosene lamps, tuk-tuks, jak trees, red earth roads, the smell of rain on hot ground, the sound of geckos and temple drums).
Craft rules:
- Open with a concrete, unsettling image or a question in the FIRST TWO SENTENCES. Never open with "Welcome" or "Today".
- Tell it as a story with named characters, one clear protagonist, a place, a time, escalating tension, a turn, and an ending that lingers (a twist or a quiet chill).
- Short, spoken sentences. Vary rhythm. Use ellipses and commas for natural pauses. Sensory detail over adjectives.
- No filler cliches (bone-deep, primal, chilling anomaly). No "subscribe and hit the bell". One light call to follow at the very end only.
HONESTY RULES (YouTube and audience trust):
- Folklore, legends and urban legends are told AS folklore ("they say", "the old people in the village say"). Invented characters are fictional.
- NEVER invent documents, police files, archives, scientific measurements or named experts. Real history may only be used if it is genuinely well known; otherwise stay vague ("older villagers say").
- Be respectful of religion, rituals, and real communities. No gore for its own sake, no real private individuals.
VISUAL PROMPT RULES (for an AI image model):
- Photographic and specific: place, light source, weather, lens, time of night. Example: "wet red-earth road through teak forest at 2am, single yellow tuk-tuk headlight beam in fog, 35mm film, long exposure".
- AVOID close-up faces and hands (AI renders them badly). Prefer silhouettes, backs of people, distant figures, doorways, lamps, objects, landscapes.
- Never request text, signs, logos, or watermarks. Each scene gets 2 DIFFERENT shots (wide + detail).
Return ONLY valid JSON."""

SCHEMA = """{
  "title": "<=70 chars, curiosity-driven, honest (no lies), includes Sri Lanka or a Sri Lankan element where natural",
  "thumbnail_text": "2-4 punchy words, uppercase",
  "description": "2 short paragraphs for YouTube: what the story is and that it is a retelling of folklore/fiction as applicable. No fake claims.",
  "fiction_note": "one sentence disclosure, e.g. 'Based on Sri Lankan folklore; characters and events are dramatised.'",
  "tags": ["10-14 relevant tags"],
  "scenes": [
    {"narration": "70-130 words of spoken story for this scene", "visuals": ["wide shot prompt", "detail shot prompt"]}
  ],
  "shorts": [
    {"title": "<=60 chars hook", "narration": "100-130 words, standalone mini-story or the most gripping moment retold with its own hook and a cliffhanger final line, ending with 'The full story is on our channel.'", "visuals": ["5 different vertical-friendly shot prompts"]}
  ]
}"""


def _build_prompt(story: dict, feedback: list[str] | None) -> str:
    p = f"""Write a complete episode.

STORY TOPIC: {story['title']}
KIND: {story['kind']}   (folklore / urban_legend / practice / history / legend_with_history)
SEED NOTES: {story['seed']}
VISUAL SETTING: {story['setting']}

Requirements:
- Narration total ≈ {TARGET_WORDS} words across 10-12 scenes (about {TARGET_WORDS // 140} minutes spoken).
- Exactly {SHORTS_PER_VIDEO} shorts.
- Follow this JSON schema exactly:
{SCHEMA}
"""
    if feedback:
        p += "\nYour previous draft FAILED the quality gate. Fix every one of these and rewrite fully:\n- " + "\n- ".join(feedback) + "\n"
    return p


def write_script(story: dict, max_attempts: int = 3) -> dict:
    feedback = None
    best = None
    for attempt in range(1, max_attempts + 1):
        print(f"[Script] Writing '{story['title']}' (attempt {attempt}/{max_attempts})...")
        script = llm.generate_json(_build_prompt(story, feedback), system=SYSTEM)
        problems = quality.check_script(script, story["id"])
        if not problems:
            print("[Script] Passed quality gate.")
            quality.save_script(story["id"], script)
            return script
        print("[Script] Problems:\n  - " + "\n  - ".join(problems))
        feedback = problems
        best = script
    raise RuntimeError(
        f"Script for '{story['id']}' failed the quality gate after {max_attempts} attempts; refusing to publish a weak video. "
        f"Last problems: {feedback}"
    )


def load_script_file(path) -> dict:
    """For offline tests / manual scripts."""
    return json.loads(open(path, encoding="utf-8").read())
