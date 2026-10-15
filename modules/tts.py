"""Free neural narration (Edge-TTS) with exact word timings -> perfectly synced captions and scene lengths."""
import asyncio
import re
import subprocess
import time
import wave
from pathlib import Path

import edge_tts
import imageio_ffmpeg

from config import VOICE_EN, VOICE_RATE, VOICE_PITCH

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SAMPLE_RATE = 44100


async def _synth(text: str, out_mp3: Path) -> list[tuple[float, float, str]]:
    comm = edge_tts.Communicate(text, VOICE_EN, rate=VOICE_RATE, pitch=VOICE_PITCH, boundary="WordBoundary")
    events = []
    with open(out_mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                events.append((s, s + ch["duration"] / 1e7, ch["text"]))
    return events


def synth_scene(text: str, out_mp3: Path, retries: int = 3) -> list[tuple[float, float, str]]:
    last = None
    for i in range(1, retries + 1):
        try:
            ev = asyncio.run(_synth(text, out_mp3))
            if out_mp3.stat().st_size > 2000:
                return ev
        except Exception as e:
            last = e
            print(f"  [TTS retry {i}/{retries}] {type(e).__name__}: {str(e)[:120]}")
        time.sleep(3 * i)
    raise RuntimeError(f"TTS failed: {last}")


def _to_wav(mp3: Path, wav: Path, tail_silence: float) -> float:
    """mp3 -> mono 44.1k wav with `tail_silence` seconds of silence appended. Returns duration."""
    subprocess.run(
        [FFMPEG, "-y", "-i", str(mp3), "-ac", "1", "-ar", str(SAMPLE_RATE), "-af", f"apad=pad_dur={tail_silence}", str(wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(wavs: list[Path], out: Path) -> None:
    with wave.open(str(wavs[0]), "rb") as w0:
        params = w0.getparams()
    with wave.open(str(out), "wb") as wo:
        wo.setparams(params)
        for p in wavs:
            with wave.open(str(p), "rb") as wi:
                wo.writeframes(wi.readframes(wi.getnframes()))


def narrate(texts: list[str], workdir: Path, label: str, gap: float = 0.55) -> dict:
    """Synthesize each text block. Returns {wav, durations, starts, events(abs timings), total}."""
    workdir.mkdir(parents=True, exist_ok=True)
    wavs, durations, starts, all_events = [], [], [], []
    t0 = 0.0
    for i, text in enumerate(texts, 1):
        mp3 = workdir / f"{label}_{i:02d}.mp3"
        wav = workdir / f"{label}_{i:02d}.wav"
        print(f"  [Voice] {label} block {i}/{len(texts)} ({len(text.split())} words)")
        ev = synth_scene(text, mp3)
        dur = _to_wav(mp3, wav, gap)
        starts.append(t0)
        durations.append(dur)
        all_events += [(t0 + s, t0 + e, w) for s, e, w in ev]
        wavs.append(wav)
        t0 += dur
    full = workdir / f"{label}_narration.wav"
    concat_wavs(wavs, full)
    return {"wav": full, "durations": durations, "starts": starts, "events": all_events, "total": t0}


# ---------------- captions (ASS) ----------------
def _ts(t: float) -> str:
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def build_ass(events: list[tuple[float, float, str]], size: tuple[int, int], out: Path, max_words: int, upper: bool) -> Path:
    w, h = size
    vertical = h > w
    fontsize = 84 if vertical else 56
    margin_v = int(h * (0.30 if vertical else 0.09))
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Cap,Arial,{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{4 if vertical else 3},1,2,{int(w*0.07)},{int(w*0.07)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines, cur, cur_start, cur_end = [], [], None, None

    def flush():
        nonlocal cur, cur_start, cur_end
        if cur:
            lines.append((cur_start, cur_end, " ".join(cur)))
        cur, cur_start, cur_end = [], None, None

    for s, e, text in events:
        if cur and (s - cur_end > 0.6):
            flush()
        if cur_start is None:
            cur_start = s
        cur.append(text.strip())
        cur_end = e
        if len(" ".join(cur).split()) >= max_words or re.search(r"[.?!…;:]$", text.strip()):
            flush()
    flush()

    out_lines = []
    for i, (s, e, text) in enumerate(lines):
        nxt = lines[i + 1][0] if i + 1 < len(lines) else e + 0.4
        end = min(max(e + 0.25, s + 0.5), nxt - 0.02) if nxt > s + 0.5 else e
        text = text.upper() if upper else text
        text = text.replace("\\", "").replace("{", "(").replace("}", ")")
        out_lines.append(f"Dialogue: 0,{_ts(s)},{_ts(end)},Cap,,0,0,0,,{text}")
    out.write_text(header + "\n".join(out_lines) + "\n", encoding="utf-8")
    return out
