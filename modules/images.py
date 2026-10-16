"""Free image generation with automatic provider fallback.

Pollinations changed in 2026: the anonymous endpoint now answers 402/401, so a (free-tier) API key is needed.
Chain (configurable via IMAGE_PROVIDERS): pollinations -> cloudflare -> huggingface -> horde (works with NO key, but slow/soft).
"""
import base64
import io
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image

from config import (CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, HF_TOKEN, HORDE_API_KEY,
                    IMAGE_PROVIDERS, POLLINATIONS_API_KEY)
from modules.quality import check_image

STYLE = ("photorealistic cinematic night photography, shot on 35mm film, fine film grain, muted desaturated colour grade, "
         "deep shadows, volumetric fog, moody low-key lighting, authentic Sri Lankan setting, no text, no watermark, no logo, no close-up face")

_disabled: set[str] = set()
_fail_count: dict[str, int] = {}


def _fit(data: bytes, size: tuple[int, int]) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = size
    scale = max(tw / im.width, th / im.height)
    im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    im = im.crop((left, top, left + tw, top + th))
    if scale > 1.3:   # strong upscale (e.g. square 1024px model output -> 16:9 or 9:16): restore some crispness
        from PIL import ImageFilter
        im = im.filter(ImageFilter.UnsharpMask(radius=2.2, percent=90, threshold=2))
    return im


def _pollinations(prompt, size, seed):
    if not POLLINATIONS_API_KEY:
        raise RuntimeError("POLLINATIONS_API_KEY not set")
    url = (f"https://gen.pollinations.ai/image/{urllib.parse.quote(prompt)}"
           f"?model=flux&width={size[0]}&height={size[1]}&seed={seed}&nologo=true")
    r = requests.get(url, headers={"Authorization": f"Bearer {POLLINATIONS_API_KEY}"}, timeout=120)
    r.raise_for_status()
    return r.content


def _cloudflare(prompt, size, seed):
    if not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN):
        raise RuntimeError("Cloudflare credentials not set")
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    r = requests.post(url, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
                      json={"prompt": prompt[:2000], "steps": 6}, timeout=180)   # API rejects seed/width/height; output is 1024x1024
    r.raise_for_status()
    return base64.b64decode(r.json()["result"]["image"])


def _huggingface(prompt, size, seed):
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set")
    url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    w, h = (size[0] // 64 * 64, size[1] // 64 * 64)
    r = requests.post(url, headers={"Authorization": f"Bearer {HF_TOKEN}"},
                      json={"inputs": prompt, "parameters": {"width": min(w, 1344), "height": min(h, 1344), "seed": seed}}, timeout=180)
    r.raise_for_status()
    return r.content


def _horde(prompt, size, seed):
    landscape = size[0] >= size[1]
    w, h = (640, 384) if landscape else (384, 640)   # anonymous-friendly size; upscaled after
    hdr = {"apikey": HORDE_API_KEY, "Client-Agent": "yt-horror-pipeline:1.0:local"}
    body = {"prompt": prompt + " ### text, watermark, face, deformed, cartoon", "nsfw": False, "censor_nsfw": True, "r2": True,
            "params": {"width": w, "height": h, "steps": 25, "n": 1, "seed": str(seed), "sampler_name": "k_euler_a", "cfg_scale": 7}}
    r = requests.post("https://aihorde.net/api/v2/generate/async", headers=hdr, json=body, timeout=60)
    r.raise_for_status()
    rid = r.json()["id"]
    for _ in range(90):                                  # up to ~7.5 min
        time.sleep(5)
        c = requests.get(f"https://aihorde.net/api/v2/generate/check/{rid}", timeout=30).json()
        if c.get("done"):
            break
        if c.get("faulted"):
            raise RuntimeError("horde faulted")
    else:
        raise RuntimeError("horde timeout")
    st = requests.get(f"https://aihorde.net/api/v2/generate/status/{rid}", timeout=30).json()
    img = st["generations"][0]["img"]
    return requests.get(img, timeout=60).content


_PROVIDERS = {"pollinations": _pollinations, "cloudflare": _cloudflare, "huggingface": _huggingface, "horde": _horde}


def generate(prompt: str, out: Path, size: tuple[int, int], seed: int) -> bool:
    """Try providers in order. Returns True if a valid image was saved at `out`."""
    if check_image(out):
        return True
    full_prompt = f"{prompt}. {STYLE}"
    for name in IMAGE_PROVIDERS:
        if name in _disabled or name not in _PROVIDERS:
            continue
        try:
            data = _PROVIDERS[name](full_prompt, size, seed)
            _fit(data, size).save(out, "JPEG", quality=93)
            if check_image(out):
                _fail_count[name] = 0
                print(f"    [Image OK via {name}] {out.name}")
                return True
            raise RuntimeError("image failed sanity check (blank/tiny)")
        except Exception as e:
            _fail_count[name] = _fail_count.get(name, 0) + 1
            msg = str(e)[:110]
            print(f"    [Image {name} failed ({_fail_count[name]})] {msg}")
            if "not set" in msg or _fail_count[name] >= 3:
                _disabled.add(name)
                print(f"    [Image] provider '{name}' disabled for this run")
    return False


def generate_many(prompts: list[str], folder: Path, prefix: str, size: tuple[int, int], base_seed: int) -> list[Path]:
    """Generate one image per prompt. Missing ones are filled from neighbours (re-cropped later) so the video never breaks."""
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path | None] = []
    for i, p in enumerate(prompts):
        out = folder / f"{prefix}_{i + 1:02d}.jpg"
        ok = generate(p, out, size, base_seed + i * 17)
        paths.append(out if ok else None)
        time.sleep(1.5)
    good = [p for p in paths if p]
    if not good:
        raise RuntimeError("No image provider worked. Set POLLINATIONS_API_KEY (free), CLOUDFLARE_* or HF_TOKEN in .env, or check your internet.")
    filled, last = [], good[0]
    for p in paths:
        last = p or last
        filled.append(last)
    return filled
