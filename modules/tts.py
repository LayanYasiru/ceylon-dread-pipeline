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


def _num(s: str) -> int:
    return int(re.sub(r"[^\d+-]", "", s) or 0)


async def _synth(text: str, out_mp3: Path, rate: str | None = None, pitch: str | None = None) -> list[tuple[float, float, str]]:
    comm = edge_tts.Communicate(text, VOICE_EN, rate=rate or VOICE_RATE, pitch=pitch or VOICE_PITCH, boundary="WordBoundary")
    events = []
    with open(out_mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                events.append((s, s + ch["duration"] / 1e7, ch["text"]))
    return events


def synth_scene(text: str, out_mp3: Path, retries: int = 3, rate: str | None = None, pitch: str | None = None) -> list[tuple[float, float, str]]:
    last = None
    for i in range(1, retries + 1):
        try:
            ev = asyncio.run(_synth(text, out_mp3, rate, pitch))
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


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _pause_after(sentence: str, last: bool, rng) -> float:
    """Human storytellers do not leave identical gaps: longer after questions and trailing-off lines, shorter after short beats."""
    s = sentence.strip()
    if last:
        return rng.uniform(0.75, 1.0)
    if s.endswith("…") or s.endswith("..."):
        return rng.uniform(0.85, 1.2)
    if s.endswith("?"):
        return rng.uniform(0.55, 0.8)
    if len(s.split()) <= 4:
        return rng.uniform(0.3, 0.45)
    return rng.uniform(0.32, 0.55)


def _natural_block(text: str, workdir: Path, label: str, i: int, rng) -> tuple[Path, float, list]:
    """One scene -> sentence-by-sentence synthesis with small random rate/pitch variation and human-like pauses."""
    base_rate, base_pitch = _num(VOICE_RATE), _num(VOICE_PITCH)
    wavs, events, t = [], [], 0.0
    sents = _sentences(text)
    for k, sent in enumerate(sents):
        words = len(sent.split())
        rate = base_rate + rng.uniform(-3.5, 2.5) - (4 if words <= 5 else 0)       # short dramatic lines slower
        pitch = base_pitch + rng.uniform(-2.0, 2.0) + (2 if sent.endswith("?") else 0) - (2 if words <= 5 else 0)
        mp3 = workdir / f"{label}_{i:02d}_{k:02d}.mp3"
        wav = workdir / f"{label}_{i:02d}_{k:02d}.wav"
        ev = synth_scene(sent, mp3, rate=f"{rate:+.0f}%", pitch=f"{pitch:+.0f}Hz")
        dur = _to_wav(mp3, wav, _pause_after(sent, k == len(sents) - 1, rng))
        events += [(t + s, t + e, w) for s, e, w in ev]
        wavs.append(wav)
        t += dur
    block = workdir / f"{label}_{i:02d}.wav"
    concat_wavs(wavs, block)
    return block, t, events


def _narrate_edge(texts: list[str], workdir: Path, label: str, gap: float = 0.55) -> dict:
    """Synthesize each text block. Returns {wav, durations, starts, events(abs timings), total}."""
    import random
    workdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(label + str(len(texts)))
    wavs, durations, starts, all_events = [], [], [], []
    t0 = 0.0
    for i, text in enumerate(texts, 1):
        print(f"  [Voice] {label} block {i}/{len(texts)} ({len(text.split())} words)")
        wav, dur, ev = _natural_block(text, workdir, label, i, rng)
        starts.append(t0)
        durations.append(dur)
        all_events += [(t0 + s, t0 + e, w) for s, e, w in ev]
        wavs.append(wav)
        t0 += dur
    full = workdir / f"{label}_narration.wav"
    concat_wavs(wavs, full)
    return {"wav": full, "durations": durations, "starts": starts, "events": all_events, "total": t0}


# ---------------- Gemini TTS: expressive storyteller (whisper, slow-downs) ----------------
STORY_DIRECTION = (
    "You are an old Sri Lankan storyteller telling a ghost story by lamplight to a small circle of listeners, late at night. "
    "Speak slowly and intimately, in a low calm voice with a warm, slightly gravelly tone. Let the dread build gradually. "
    "Keep a steady overall pace of about 125 words per minute: slow down only briefly on the single most frightening phrase of a passage, never drag the whole passage, and use short pauses. Sound like a real person remembering something "
    "they wish they could forget. Read ONLY the transcript, do not add or skip words."
)


def _mood(label: str, i: int, n: int) -> str:
    if label.startswith("short"):
        return "This is a short teaser: begin with an immediate hook, keep tension tight and urgent, end the final line in a hushed whisper."
    if i == 1:
        return "This is the opening of the story: calm, inviting, a little curious, drawing the listener in."
    if i == n:
        return "This is the ending: quiet and haunted. Whisper the final two sentences, letting only the very last words slow and trail off."
    if i >= n - 1:
        return "The terror is peaking: drop your voice low and whisper the most frightening line or two."
    if i >= n // 2:
        return "The tension is rising: hushed voice, a short pause before each reveal, and whisper only the supernatural moment."
    return "The story is building: steady, intimate, a growing unease under the calm."


class QuotaExhausted(Exception):
    """Daily free-tier quota of a Gemini TTS model is used up."""


def _quota_file():
    from config import DATA_DIR
    return DATA_DIR / "tts_quota.json"


def _pacific_day() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()   # Google free-tier days reset at Pacific midnight


def _quota_load() -> dict:
    import json
    try:
        q = json.loads(_quota_file().read_text(encoding="utf-8"))
    except Exception:
        q = {}
    if q.get("day") != _pacific_day():
        q = {"day": _pacific_day(), "calls": {}}
    return q


def _quota_add(model: str, n: int = 1, exhausted: bool = False) -> None:
    import json

    from config import GEMINI_TTS_DAILY_LIMIT
    q = _quota_load()
    q["calls"][model] = GEMINI_TTS_DAILY_LIMIT if exhausted else q["calls"].get(model, 0) + n
    _quota_file().write_text(json.dumps(q, indent=1), encoding="utf-8")


def _pick_models(calls_needed: int) -> list[str]:
    """Models that still have enough free calls today for this whole video, in priority order."""
    from config import GEMINI_TTS_DAILY_LIMIT, GEMINI_TTS_MODELS
    used = _quota_load()["calls"]
    return [m for m in GEMINI_TTS_MODELS if used.get(m, 0) + calls_needed <= GEMINI_TTS_DAILY_LIMIT]


def _gemini_wav(text: str, mood: str, out_wav: Path, model: str, retries: int = 3) -> None:
    import wave as _w

    from google import genai
    from google.genai import types

    from config import GEMINI_API_KEY, GEMINI_TTS_VOICE
    client = genai.Client(api_key=GEMINI_API_KEY)
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = client.models.generate_content(
                model=model,
                contents=f"{STORY_DIRECTION} {mood}\n\nTranscript:\n{text}",
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_TTS_VOICE))),
                ),
            )
            pcm = r.candidates[0].content.parts[0].inline_data.data
            if len(pcm) < 24000 * 2 * 2:
                raise RuntimeError("audio too short")
            with _w.open(str(out_wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            _quota_add(model)
            return
        except Exception as e:
            msg = str(e)
            if "429" in msg and ("PerDay" in msg or "limit: 0" in msg):
                _quota_add(model, exhausted=True)
                raise QuotaExhausted(model)
            last = e
            print(f"    [Gemini TTS {model} retry {attempt}/{retries}] {type(e).__name__}: {msg[:110]}")
            time.sleep(8 * attempt)
    raise RuntimeError(f"Gemini TTS failed: {last}")


def _silences(wav: Path) -> list[tuple[float, float]]:
    res = subprocess.run([FFMPEG, "-i", str(wav), "-af", "silencedetect=noise=-36dB:d=0.25", "-f", "null", "-"],
                         stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    return [(s, ends[k] if k < len(ends) else s + 0.3) for k, s in enumerate(starts)]


def _align_words(text: str, wav: Path, dur: float) -> list[tuple[float, float, str]]:
    """Gemini TTS returns audio only, so word times are estimated: words are spread by length across the spoken region and a
    tracker snaps each sentence end to the nearest real pause (silence detection), re-estimating the speaking rate as it goes
    so long chunks do not drift."""
    import numpy as np
    sil = _silences(wav)
    t0 = sil[0][1] if sil and sil[0][0] < 0.08 else 0.0
    t_end = sil[-1][0] if sil and sil[-1][1] >= dur - 0.08 else dur
    t_end = max(t_end, t0 + 0.5)
    words = text.split()
    if not words:
        return []
    pos, wstart, wend, gaps = 0.0, [], [], []
    for wd in words:
        weight = len(re.sub(r"\W", "", wd)) + 1.5
        wstart.append(pos)
        pos += weight
        wend.append(pos)
        gap = 5.0 if re.search(r"[.?!…]$", wd) else (1.8 if wd.endswith((",", ";", ":", "—")) else 0.0)
        gaps.append(gap)
        pos += gap
    total_w = pos
    rate = (t_end - t0) / total_w
    cur_t, cur_w = t0, 0.0
    anchors = [(0.0, t0)]
    used = set()
    for k, wd in enumerate(words[:-1]):
        if not (re.search(r"[.?!…]$", wd) and gaps[k] > 0):
            continue
        p_mid = wend[k] + gaps[k] / 2
        predicted = cur_t + rate * (p_mid - cur_w)
        window = max(1.0, 0.25 * (predicted - cur_t))
        best = None
        for si, (a, b) in enumerate(sil):
            mid = (a + b) / 2
            if si in used or mid <= cur_t + 0.15 or abs(mid - predicted) > window:
                continue
            if best is None or abs(mid - predicted) < abs(best[1] - predicted):
                best = (si, mid)
        if best:
            used.add(best[0])
            anchors.append((p_mid, best[1]))
            rate = 0.6 * rate + 0.4 * (best[1] - cur_t) / max(p_mid - cur_w, 1e-6)
            cur_t, cur_w = best[1], p_mid
    anchors.append((total_w, max(t_end, anchors[-1][1] + 0.05)))
    aw = np.array([a for a, _ in anchors])
    at = np.maximum.accumulate(np.array([b for _, b in anchors]))
    return [(float(np.interp(wstart[k], aw, at)), float(np.interp(wend[k], aw, at)), words[k]) for k in range(len(words))]


def _chunks(texts: list[str], max_words: int = 270) -> list[list[int]]:
    """Group consecutive scenes so one TTS call covers ~2 scenes (the free tier allows few calls per day)."""
    groups, cur, cnt = [], [], 0
    for i, t in enumerate(texts):
        n = len(t.split())
        if cur and cnt + n > max_words:
            groups.append(cur)
            cur, cnt = [], 0
        cur.append(i)
        cnt += n
    if cur:
        groups.append(cur)
    return groups


def _narrate_gemini(texts: list[str], workdir: Path, label: str, model: str) -> dict:
    import random
    rng = random.Random(label + model)
    workdir.mkdir(parents=True, exist_ok=True)
    groups = _chunks(texts)
    n_scenes = len(texts)
    chunk_wavs, scene_t, all_events, t0 = [], [None] * n_scenes, [], 0.0
    for gi, grp in enumerate(groups, 1):
        text = "\n\n".join(texts[i] for i in grp)
        print(f"  [Voice:{model}] {label} chunk {gi}/{len(groups)} (scenes {grp[0] + 1}-{grp[-1] + 1}, {len(text.split())} words)")
        raw = workdir / f"{label}_c{gi:02d}_g24.wav"
        _gemini_wav(text, _mood(label, grp[0] + 1, n_scenes) if len(grp) == 1 else _mood(label, grp[len(grp) // 2] + 1, n_scenes), raw, model)
        conv = workdir / f"{label}_c{gi:02d}_conv.wav"
        subprocess.run([FFMPEG, "-y", "-i", str(raw), "-ac", "1", "-ar", str(SAMPLE_RATE), str(conv)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(str(conv), "rb") as w:
            dur_speech = w.getnframes() / w.getframerate()
        wpm = len(text.split()) / (dur_speech / 60)
        if wpm < 105:      # safety net: the model sometimes over-applies "slow"; keep the story moving (pitch is preserved)
            factor = min(1.45, 105 / wpm)
            fast = workdir / f"{label}_c{gi:02d}_fast.wav"
            subprocess.run([FFMPEG, "-y", "-i", str(conv), "-af", f"atempo={factor:.3f}", str(fast)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            conv = fast
            with wave.open(str(conv), "rb") as w:
                dur_speech = w.getnframes() / w.getframerate()
            print(f"    [pace] {wpm:.0f} wpm was too slow -> sped x{factor:.2f}")
        ev = _align_words(text, conv, dur_speech)
        tail = rng.uniform(0.7, 1.0)
        wav = workdir / f"{label}_c{gi:02d}.wav"
        with wave.open(str(conv), "rb") as wi, wave.open(str(wav), "wb") as wo:
            wo.setparams(wi.getparams())
            wo.writeframes(wi.readframes(wi.getnframes()))
            wo.writeframes(b"\x00\x00" * int(SAMPLE_RATE * tail))
        # scene start = time of the scene's first word inside this chunk
        idx = 0
        for i in grp:
            scene_t[i] = t0 + (ev[idx][0] if idx < len(ev) else 0.0)
            idx += len(texts[i].split())
        scene_t[grp[0]] = t0                                  # first scene of a chunk starts at the chunk start
        all_events += [(t0 + s, t0 + e, w) for s, e, w in ev]
        chunk_wavs.append(wav)
        t0 += dur_speech + tail
    full = workdir / f"{label}_narration.wav"
    concat_wavs(chunk_wavs, full)
    starts = [float(x) for x in scene_t]
    durations = [(starts[i + 1] if i + 1 < n_scenes else t0) - starts[i] for i in range(n_scenes)]
    return {"wav": full, "durations": durations, "starts": starts, "events": all_events, "total": t0}


def narrate(texts: list[str], workdir: Path, label: str, gap: float = 0.55) -> dict:
    """Expressive Gemini TTS first (one model per video, rotating through the free models that still have quota today).
    If every model is exhausted or anything fails, the WHOLE video uses Edge-TTS so the voice stays consistent."""
    from config import GEMINI_API_KEY, TTS_PROVIDER
    if TTS_PROVIDER == "gemini" and GEMINI_API_KEY:
        need = len(_chunks(texts))
        for model in _pick_models(need):
            try:
                return _narrate_gemini(texts, workdir, label, model)
            except QuotaExhausted as e:
                print(f"  [Voice] {e} daily quota used up; trying the next model")
            except Exception as e:
                print(f"  [Voice] Gemini TTS error on {model} ({str(e)[:120]}); trying the next model")
        print("  [Voice] no Gemini TTS model available today -> Edge-TTS for this video")
    return _narrate_edge(texts, workdir, label, gap)

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
