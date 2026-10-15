"""Cinematic renderer (pure FFmpeg): Ken Burns motion, grade, grain, vignette, burned-in captions,
procedural ambience that ducks under the narration, loudness-normalised to YouTube's -14/-16 LUFS range."""
import hashlib
import random
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

from config import FPS

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str], cwd: Path | None = None, what: str = "ffmpeg") -> None:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"{what} failed (exit {res.returncode}):\n{res.stderr[-1200:]}")


def _motion(idx: int, frames: int) -> str:
    """Rotate through four camera moves so consecutive shots never feel identical."""
    n = max(frames, 2)
    kind = idx % 4
    if kind == 0:   # slow push in
        return f"z='1+0.16*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if kind == 1:   # pull out
        return f"z='1.16-0.16*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if kind == 2:   # drift left -> right
        return f"z='1.14':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'"
    return f"z='1.14':x='(iw-iw/zoom)*(1-on/{n})':y='(ih-ih/zoom)*0.35'"   # drift right -> left


def render_segment(img: Path, dur: float, size: tuple[int, int], idx: int, out: Path, fade: float) -> Path:
    w, h = size
    frames = max(int(round(dur * FPS)), 2)
    fade = min(fade, dur / 3)
    vf = (
        f"scale={int(w * 1.5)}:{int(h * 1.5)}:force_original_aspect_ratio=increase,crop={int(w * 1.5)}:{int(h * 1.5)},"
        f"zoompan={_motion(idx, frames)}:d={frames}:s={w}x{h}:fps={FPS},"
        "eq=saturation=0.82:contrast=1.07:brightness=-0.035:gamma=0.97,"
        "noise=alls=7:allf=t,vignette=PI/4.3,"
        f"fade=t=in:st=0:d={fade:.2f},fade=t=out:st={max(dur - fade, 0):.2f}:d={fade:.2f},format=yuv420p"
    )
    _run([FFMPEG, "-y", "-i", str(img), "-vf", vf, "-frames:v", str(frames), "-r", str(FPS),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out)], what=f"segment {out.name}")
    return out


def _stings(seed: int, total: float, starts: list[float]) -> list[tuple[float, float]]:
    """A few low 'impact' hits at scene boundaries: (time, frequency). Deterministic per story, different between stories."""
    rnd = random.Random(seed)
    if len(starts) < 5:
        return []
    picks = sorted({rnd.randrange(2, len(starts) - 1) for _ in range(4)})
    return [(starts[i], rnd.choice([38, 42, 46, 52])) for i in picks if starts[i] < total - 3]


def final_mix_and_captions(segments: list[Path], narration_wav: Path, ass: Path, total: float, seed: int,
                           scene_starts: list[float], out: Path, workdir: Path) -> Path:
    """Concat segments, burn captions, mix voice + ambience (ducked), normalise loudness, write the final mp4."""
    concat = workdir / "segments.txt"
    concat.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    silent = workdir / "silent_video.mp4"
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)], what="concat")

    rnd = random.Random(seed)
    base = rnd.choice([48, 55, 61, 66])          # drone root differs per video
    T = f"{total:.2f}"
    stings = _stings(seed, total, scene_starts)

    inputs = ["-i", str(silent), "-i", str(narration_wav),
              "-f", "lavfi", "-t", T, "-i", "anoisesrc=c=brown:r=44100:a=0.6",
              "-f", "lavfi", "-t", T, "-i",
              f"aevalsrc='0.45*sin(2*PI*{base}*t)+0.35*sin(2*PI*{base * 1.007:.3f}*t)+0.18*sin(2*PI*{base * 1.5:.2f}*t)*(0.5+0.5*sin(2*PI*0.04*t))':s=44100:d={T}"]
    for _, f in stings:
        inputs += ["-f", "lavfi", "-t", "3", "-i", f"aevalsrc='0.9*sin(2*PI*{f}*t)*exp(-1.6*t)+0.25*sin(2*PI*{f * 2.01:.2f}*t)*exp(-3*t)':s=44100:d=3"]

    fc = [
        f"[0:v]ass={ass.name}[v]",
        "[1:a]highpass=f=70,acompressor=threshold=0.12:ratio=3:attack=5:release=120,equalizer=f=200:t=q:w=1.0:g=2,"
        "aecho=0.8:0.55:70:0.12,asplit=2[voice][sc]",
        "[2:a]lowpass=f=420,highpass=f=35,tremolo=f=0.12:d=0.55,volume=0.55[wind]",
        "[3:a]lowpass=f=180,volume=0.5[drone]",
    ]
    mix_in = "[wind][drone]"
    n_mix = 2
    for k, (t, _) in enumerate(stings):
        ms = int(t * 1000)
        fc.append(f"[{4 + k}:a]adelay={ms}|{ms},volume=0.55[st{k}]")
        mix_in += f"[st{k}]"
        n_mix += 1
    fc.append(f"{mix_in}amix=inputs={n_mix}:normalize=0:duration=longest,volume=0.9[bed]")
    fc.append("[bed][sc]sidechaincompress=threshold=0.02:ratio=10:attack=30:release=500[ducked]")
    fc.append("[voice][ducked]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-15:TP=-2:LRA=9,alimiter=limit=0.89[a]")

    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-maxrate", "5M", "-bufsize", "10M", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-t", T, "-movflags", "+faststart", str(out)],
         cwd=ass.parent, what="final mix")
    return out


def _split_points(events, start: float, end: float, parts: int) -> list[float]:
    """Pick image-change times at sentence ends nearest to equal divisions of [start, end]."""
    sentence_ends = [e for _, e, w in events if start < e < end - 1.0 and re.search(r"[.?!…]$", w.strip())]
    points = []
    for k in range(1, parts):
        target = start + (end - start) * k / parts
        points.append(min(sentence_ends, key=lambda t: abs(t - target)) if sentence_ends else target)
    return sorted(points)


def build_timeline(starts: list[float], durations: list[float], visuals_per_scene: list[int], events) -> list[tuple[int, float]]:
    """Return [(scene_index, duration_of_this_shot), ...] aligning image changes to the narration."""
    shots = []
    for i, (s, d, n) in enumerate(zip(starts, durations, visuals_per_scene)):
        end = s + d
        cuts = [s] + _split_points(events, s, end, n) + [end]
        for a, b in zip(cuts, cuts[1:]):
            shots.append((i, b - a))
    return shots


def seed_from(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)
