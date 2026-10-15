"""2.5D animation from a single still, 100% free and CPU-only.

 1. Depth map (Depth-Anything-V2-Small via transformers; falls back to a synthetic ground-plane depth)
 2. Per-frame depth-aware camera move: near things move more than far things (real parallax, not a flat zoom)
 3. Living atmosphere: two drifting fog layers (thicker in the distance), floating dust, flickering lamps/fire, handheld shake
 Frames are piped to ffmpeg, which adds grade, film grain, vignette and fades.
"""
import math
import os
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from config import FPS, FOG_STRENGTH

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
_pipe = None
_warned = False


# ---------------- depth ----------------
def _synthetic_depth(h: int, w: int) -> np.ndarray:
    """Ground-plane guess: lower in the frame = nearer, centre slightly nearer. Better than nothing if the model is unavailable."""
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    return np.clip(0.15 + 0.75 * y + 0.1 * (1 - np.abs(x)), 0, 1) * np.ones((1, w), np.float32)


def depth_for(img: Path) -> Path:
    """Compute (once) and cache the depth map next to the image. Call this in the MAIN process before rendering."""
    global _pipe, _warned
    npy = img.with_suffix(".depth.npy")
    if npy.exists():
        return npy
    from PIL import Image
    try:
        if _pipe is None:
            from transformers import pipeline
            _pipe = pipeline("depth-estimation", model=DEPTH_MODEL, device=-1)
        im = Image.open(img).convert("RGB")
        im.thumbnail((700, 700))
        d = np.asarray(_pipe(im)["depth"], dtype=np.float32)     # bright = near
        lo, hi = np.percentile(d, 2), np.percentile(d, 98)
        d = np.clip((d - lo) / (hi - lo + 1e-6), 0, 1)
    except Exception as e:
        if not _warned:
            print(f"  [Depth] model unavailable ({type(e).__name__}: {str(e)[:90]}); using synthetic depth")
            _warned = True
        d = _synthetic_depth(256, 256)
    np.save(npy, d.astype(np.float16))
    return npy


# ---------------- atmosphere textures ----------------
def _fog_texture(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    import cv2
    base = np.zeros((h, w), np.float32)
    for size, amp in ((96, 1.0), (36, 0.55), (14, 0.25)):
        n = rng.random((max(2, h // size), max(2, w // size))).astype(np.float32)
        base += amp * cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    base = cv2.GaussianBlur(base, (0, 0), 7)
    base = (base - base.min()) / (base.max() - base.min() + 1e-6)
    return np.clip((base - 0.30) / 0.70, 0, 1) ** 1.3


def _smooth_noise(rng, n=6):
    ph = rng.random(n) * 6.28
    fr = 0.6 + rng.random(n) * 3.0
    return lambda t: float(sum(math.sin(2 * math.pi * f * t + p) for f, p in zip(fr, ph)) / n)


def _camera(idx: int, t: float, W: int):
    """(zoom, shift_x_px, shift_y_px) for normalised time t in [0,1]; four different moves in rotation."""
    e = t * t * (3 - 2 * t)
    A = 0.02 * W
    kind = idx % 4
    if kind == 0:
        return 1.0 + 0.10 * e, 0.25 * A * (e - 0.5), 0.0
    if kind == 1:
        return 1.10 - 0.10 * e, -0.25 * A * (e - 0.5), 0.0
    if kind == 2:
        return 1.05, A * (2 * e - 1), -0.15 * A * e
    return 1.05, -A * (2 * e - 1), 0.15 * A * e


# ---------------- renderer (runs inside a worker process) ----------------
def render_parallax(img: str, depth_npy: str, dur: float, size: tuple, idx: int, out: str, fade: float, seed: int) -> str:
    import cv2
    cv2.setNumThreads(1)
    W, H = size
    SS = 1.3                                   # source supersampling so zooms stay sharp
    SW, SH = int(W * SS), int(H * SS)
    n = max(int(round(dur * FPS)), 2)
    rng = np.random.default_rng(seed + idx * 7919)

    src = cv2.imread(img, cv2.IMREAD_COLOR)
    s = max(SW / src.shape[1], SH / src.shape[0])
    src = cv2.resize(src, (int(src.shape[1] * s) + 1, int(src.shape[0] * s) + 1), interpolation=cv2.INTER_CUBIC)
    y0, x0 = (src.shape[0] - SH) // 2, (src.shape[1] - SW) // 2
    src = np.ascontiguousarray(src[y0:y0 + SH, x0:x0 + SW])

    D = np.load(depth_npy).astype(np.float32)
    D = cv2.GaussianBlur(cv2.resize(D, (W, H), interpolation=cv2.INTER_CUBIC), (0, 0), 5)   # smooth = fewer tearing artefacts
    Dc = np.clip(D, 0, 1) - 0.5                                                              # -0.5 far .. +0.5 near
    X, Y = np.meshgrid(np.arange(W, dtype=np.float32) - W / 2, np.arange(H, dtype=np.float32) - H / 2)
    fog_w = (0.70 - 0.65 * Dc)[..., None].astype(np.float32)                                # more fog far away

    fog1 = _fog_texture(H, W * 2, rng)
    fog2 = _fog_texture(H, W * 2, rng)
    col = np.array([150, 148, 140], np.float32) / 255.0 * 255.0                             # cold grey fog (BGR)
    fl = _smooth_noise(rng)
    shx, shy = _smooth_noise(rng, 4), _smooth_noise(rng, 4)
    lut = (np.clip((np.arange(256) - 150) / 105.0, 0, 1) ** 2 * 255).astype(np.uint8)       # bright pixels = lamps / fire / moon

    M = 48                                                                                   # dust motes
    px, py = rng.random(M) * W, rng.random(M) * H
    pv = (rng.random(M) - 0.5) * 0.8, -(0.15 + rng.random(M) * 0.5)
    pr, pph = 1 + (rng.random(M) * 2).astype(int), rng.random(M) * 6.28
    z0 = 1.09

    post = ("eq=saturation=0.82:contrast=1.07:brightness=-0.035:gamma=0.97,noise=alls=7:allf=t,vignette=PI/4.3,"
            f"fade=t=in:st=0:d={min(fade, dur / 3):.2f},fade=t=out:st={max(dur - min(fade, dur / 3), 0):.2f}:d={min(fade, dur / 3):.2f},format=yuv420p")
    log = open(out + ".log", "wb")
    ff = subprocess.Popen(
        [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-vf", post,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", out],
        stdin=subprocess.PIPE, stdout=log, stderr=log)

    try:
        for f in range(n):
            t = f / (n - 1)
            ts = f / FPS
            z, sx, sy = _camera(idx, t, W)
            sx += 0.9 * shx(ts * 0.7)
            sy += 0.7 * shy(ts * 0.7)
            zl = z0 * (1.0 + (z - 1.0) * (1.0 + 1.6 * Dc))                 # near things zoom faster -> parallax
            mx = SW / 2 + X * (SS / zl) + (sx * SS) * (2.0 * Dc)
            my = SH / 2 + Y * (SS / zl) + (sy * SS) * (2.0 * Dc)
            fr = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            fr = fr.astype(np.float32)

            # fog: two layers drifting at different speeds
            o1, o2 = int((f * 0.9) % W), int(W - (f * 0.45) % W - 1)
            a = FOG_STRENGTH * (0.65 * fog1[:, o1:o1 + W] + 0.55 * fog2[:, o2:o2 + W])[..., None] * fog_w
            fr += (col - fr) * np.clip(a, 0, 0.75)

            # flickering lights
            gray = cv2.cvtColor(np.clip(fr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY)
            lamp = cv2.LUT(gray, lut).astype(np.float32)[..., None] / 255.0
            g = 0.10 * fl(ts) + (0.05 if rng.random() < 0.04 else 0.0)
            fr *= 1.0 + g * lamp

            # dust motes
            layer = np.zeros((H // 2, W // 2), np.float32)
            for k in range(M):
                px[k] = (px[k] + pv[0][k]) % W
                py[k] = (py[k] + pv[1][k]) % H
                tw = 0.5 + 0.5 * math.sin(ts * 1.7 + pph[k])
                cv2.circle(layer, (int(px[k] / 2), int(py[k] / 2)), int(pr[k]), float(0.35 + 0.65 * tw), -1, cv2.LINE_AA)
            layer = cv2.GaussianBlur(layer, (0, 0), 1.3)
            fr += cv2.resize(layer, (W, H), interpolation=cv2.INTER_LINEAR)[..., None] * 60.0

            ff.stdin.write(np.clip(fr, 0, 255).astype(np.uint8).tobytes())
        ff.stdin.close()
        rc = ff.wait()
    except Exception:
        ff.kill()
        raise
    finally:
        log.close()
    if rc != 0:
        raise RuntimeError(f"parallax encode failed for {out}; see {out}.log")
    os.remove(out + ".log")
    return out
