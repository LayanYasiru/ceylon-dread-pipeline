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


def make_room_ir(path: Path, seed: int) -> Path:
    """Synthesise a small, slightly dark room impulse response (decaying noise, 12 ms pre-delay) for natural-sounding reverb."""
    import wave
    import numpy as np
    sr = 44100
    n = int(sr * 0.55)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    # darker tail: low-pass the noise progressively (one-pole filter)
    out = np.zeros(n)
    y = 0.0
    for i in range(n):
        a = 0.55 + 0.4 * (i / n)
        y = a * y + (1 - a) * noise[i]
        out[i] = y
    ir = out * np.exp(-t / 0.13)
    pre = int(0.012 * sr)
    ir = np.concatenate([np.zeros(pre), ir])[:n]
    ir[pre] += 1.0 * np.abs(ir).max() * 2        # direct-sound spike so it stays articulate
    ir /= np.abs(ir).max()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((ir * 32767 * 0.9).astype(np.int16).tobytes())
    return path


def _stings(seed: int, total: float, starts: list[float]) -> list[tuple[float, float]]:
    """A few low 'impact' hits at scene boundaries: (time, frequency). Deterministic per story, different between stories."""
    rnd = random.Random(seed)
    if len(starts) < 5:
        return []
    picks = sorted({rnd.randrange(2, len(starts) - 1) for _ in range(4)})
    return [(starts[i], rnd.choice([38, 42, 46, 52])) for i in picks if starts[i] < total - 3]


def audio_graph(narration_wav: Path, total: float, seed: int, scene_starts: list[float], workdir: Path, first: int):
    """Voice + ambience graph shared by the video render and the audio-only sampler.
    `first` = ffmpeg input index of the narration file. Returns (extra ffmpeg inputs, filter list, output label)."""
    rnd = random.Random(seed)
    base = rnd.choice([48, 55, 61, 66])          # drone root differs per video
    T = f"{total:.2f}"
    stings = _stings(seed, total, scene_starts)
    ir = make_room_ir(workdir / "room_ir.wav", seed)
    f0 = first
    # inputs after narration: +1 room IR, +2 mic noise floor, +3 wind, +4 drone, +5.. stings
    inputs = ["-i", str(narration_wav), "-i", str(ir),
              "-f", "lavfi", "-t", T, "-i", "anoisesrc=c=pink:r=44100:a=0.02",
              "-f", "lavfi", "-t", T, "-i", "anoisesrc=c=pink:r=44100:a=0.30",
              "-f", "lavfi", "-t", T, "-i",
              f"aevalsrc='0.5*sin(2*PI*{base}*t)+0.2*sin(2*PI*{base * 2}*t)*(0.5+0.5*sin(2*PI*0.03*t))':s=44100:d={T}"]
    for _, f in stings:
        inputs += ["-f", "lavfi", "-t", "3", "-i", f"aevalsrc='0.8*sin(2*PI*{f}*t)*exp(-1.8*t)':s=44100:d=3"]

    # Voice: warm + natural. Gentle EQ, de-esser, light compression, then a small-room reverb (impulse response) at ~30% wet gain.
    # (The old 'aecho' gave a metallic robot sound; a real room IR does not.)
    fc = [
        f"[{f0}:a]highpass=f=85,deesser=i=0.35,equalizer=f=140:t=q:w=1.0:g=2.5,equalizer=f=320:t=q:w=1.2:g=-1.5,"
        "equalizer=f=4800:t=q:w=1.2:g=-2.5,acompressor=threshold=0.09:ratio=2.2:attack=12:release=220:makeup=1.6,asplit=3[dry][wetin][sc]",
        f"[wetin][{f0 + 1}:a]afir=dry=0:wet=1,volume=0.30[wet]",
        "[dry][wet]amix=inputs=2:normalize=0:duration=first[voice]",
        f"[{f0 + 3}:a]highpass=f=140,lowpass=f=650,tremolo=f=0.12:d=0.5,volume=0.10[wind]",
        f"[{f0 + 4}:a]lowpass=f=130,volume=0.08[drone]",
        f"[{f0 + 2}:a]highpass=f=200,lowpass=f=6000,volume=0.6[floor]",
    ]
    mix_in, n_mix = "[wind][drone][floor]", 3
    for k, (t, _) in enumerate(stings):
        ms = int(t * 1000)
        fc.append(f"[{f0 + 5 + k}:a]adelay={ms}|{ms},volume=0.22[st{k}]")
        mix_in += f"[st{k}]"
        n_mix += 1
    fc.append(f"{mix_in}amix=inputs={n_mix}:normalize=0:duration=longest[bed]")
    fc.append("[bed][sc]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=700[ducked]")
    fc.append("[voice][ducked]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-16:TP=-2:LRA=9,alimiter=limit=0.89[a]")
    return inputs, fc


def audio_only(narration_wav: Path, total: float, seed: int, scene_starts: list[float], workdir: Path, out_mp3: Path) -> Path:
    """Render just the finished audio so you can listen without waiting for a video render."""
    inputs, fc = audio_graph(narration_wav, total, seed, scene_starts, workdir, first=0)
    _run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[a]", "-t", f"{total:.2f}", "-b:a", "192k", str(out_mp3)],
         what="audio sample")
    return out_mp3


def final_mix_and_captions(segments: list[Path], narration_wav: Path, ass: Path, total: float, seed: int,
                           scene_starts: list[float], out: Path, workdir: Path) -> Path:
    """Concat segments, burn captions, mix voice + ambience (ducked), normalise loudness, write the final mp4."""
    concat = workdir / "segments.txt"
    concat.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    silent = workdir / "silent_video.mp4"
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)], what="concat")

    a_inputs, a_fc = audio_graph(narration_wav, total, seed, scene_starts, workdir, first=1)
    fc = [f"[0:v]ass={ass.name}[v]"] + a_fc
    _run([FFMPEG, "-y", "-i", str(silent), *a_inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-maxrate", "5M", "-bufsize", "10M", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)],
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
