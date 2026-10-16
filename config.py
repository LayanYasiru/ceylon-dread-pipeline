"""Central settings. Every secret comes from environment variables / .env — never hardcode keys here."""
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
WORK_DIR = OUTPUT_DIR / "work"          # per-video scratch (images, audio, segments)
READY_DIR = OUTPUT_DIR / "ready"        # finished videos / shorts / thumbnails / captions
STATE_FILE = DATA_DIR / "state.json"
GENERATED_TOPICS_FILE = DATA_DIR / "generated_topics.json"
STORY_COOLDOWN_DAYS = int(os.getenv("STORY_COOLDOWN_DAYS", "36500"))  # 36500 = a story is NEVER produced twice (lower it, e.g. 180, to allow re-tells)
TOPICS_FILE = DATA_DIR / "topics.json"
ARCHIVE_FILE = DATA_DIR / "true_history_archive.json"

for _d in (DATA_DIR, OUTPUT_DIR, WORK_DIR, READY_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str = "") -> str:
    # strip whitespace plus invisible BOM / zero-width chars that sneak in when secrets are pasted or piped
    return os.getenv(name, default).replace("﻿", "").replace("​", "").strip()


# ---- Script writer (free Gemini API key: https://aistudio.google.com/apikey) ----
GEMINI_API_KEY = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")

# ---- Voice: Gemini TTS (free key, expressive: whisper / slow-downs) with automatic Edge-TTS fallback ----
TTS_PROVIDER = env("TTS_PROVIDER", "gemini")           # gemini | edge
GEMINI_TTS_MODEL = env("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
GEMINI_TTS_VOICE = env("GEMINI_TTS_VOICE", "Algenib")  # gravelly, older storyteller
# Free tier = 10 requests/day PER MODEL, so a video uses ONE model and the pipeline rotates through the models that have quota left.
GEMINI_TTS_MODELS = [m.strip() for m in env("GEMINI_TTS_MODELS", "gemini-3.8-flash-tts,gemini-3.1-flash-tts-preview,gemini-3.8-flash-lite-tts").split(",") if m.strip()]
GEMINI_TTS_DAILY_LIMIT = int(env("GEMINI_TTS_DAILY_LIMIT", "10"))
# ---- Voice fallback (Edge-TTS is free, no key) ----
VOICE_EN = env("VOICE_EN", "en-GB-RyanNeural")   # try en-IN-PrabhatNeural for a South-Asian accent
VOICE_RATE = env("VOICE_RATE", "-8%")
VOICE_PITCH = env("VOICE_PITCH", "-4Hz")

# ---- Image providers, tried in this order (see modules/images.py) ----
IMAGE_PROVIDERS = [p.strip() for p in env("IMAGE_PROVIDERS", "pollinations,cloudflare,huggingface,horde").split(",") if p.strip()]
POLLINATIONS_API_KEY = env("POLLINATIONS_API_KEY")     # free tier: https://enter.pollinations.ai
CLOUDFLARE_ACCOUNT_ID = env("CLOUDFLARE_ACCOUNT_ID")   # Workers AI free daily allowance
CLOUDFLARE_API_TOKEN = env("CLOUDFLARE_API_TOKEN")
HF_TOKEN = env("HF_TOKEN")                             # Hugging Face free inference (FLUX.1-schnell)
HORDE_API_KEY = env("HORDE_API_KEY", "0000000000")     # anonymous works, just slower

# ---- Production shape ----
VIDEOS_PER_RUN = int(env("VIDEOS_PER_RUN", "2"))
SHORTS_PER_VIDEO = int(env("SHORTS_PER_VIDEO", "2"))
TARGET_WORDS = int(env("TARGET_WORDS", "1300"))        # ~9-10 min at the default voice rate
ANIMATION = env("ANIMATION", "parallax")               # parallax (2.5D + fog/dust) | kenburns (fast fallback)
FOG_STRENGTH = float(env("FOG_STRENGTH", "0.30"))
FPS = 24
LONG_SIZE = (1920, 1080)
SHORT_SIZE = (1080, 1920)

# ---- Publishing ----
# scheduled = upload private + YouTube publishes at the slot time (gives you a review window)
# public    = go live immediately      private = upload and leave private
PUBLISH_MODE = env("PUBLISH_MODE", "scheduled")
PUBLISH_SLOTS_UTC = [s.strip() for s in env("PUBLISH_SLOTS_UTC", "06:30,13:30").split(",") if s.strip()]  # 12:00 & 19:00 Sri Lanka
YOUTUBE_TOKEN_JSON = env("YOUTUBE_TOKEN_JSON")         # contents of token.json (made by setup_youtube_auth.py)
YOUTUBE_TOKEN_FILE = BASE_DIR / "token.json"
FACEBOOK_PAGE_ID = env("FACEBOOK_PAGE_ID")
FACEBOOK_ACCESS_TOKEN = env("FACEBOOK_ACCESS_TOKEN")   # long-lived PAGE access token
TIKTOK_ACCESS_TOKEN = env("TIKTOK_ACCESS_TOKEN")       # optional; uploads to your TikTok inbox as a draft
CHANNEL_NAME = env("CHANNEL_NAME", "Ceylon Dread")
