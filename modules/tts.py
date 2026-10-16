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


def _gemini_wav(text: str, mood: str, out_wav: Path, retries: int = 4) -> None:
    import wave as _w

    from google import genai
    from google.genai import types

    from config import GEMINI_API_KEY, GEMINI_TTS_MODEL, GEMINI_TTS_VOICE
    client = genai.Client(api_key=GEMINI_API_KEY)
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = client.models.generate_content(
                model=GEMINI_TTS_MODEL,
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
            return
        except Exception as e:
            last = e
            print(f"    [Gemini TTS retry {attempt}/{retries}] {type(e).__name__}: {str(e)[:110]}")
            time.sleep(6 * attempt)
    raise RuntimeError(f"Gemini TTS failed: {last}")


def _silences(wav: Path) -> list[tuple[float, float]]:
    res = subprocess.run([FFMPEG, "-i", str(wav), "-af", "silencedetect=noise=-36dB:d=0.25", "-f", "null", "-"],
                         stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    out = []
    for k, s in enumerate(starts):
        out.append((s, ends[k] if k < len(ends) else s + 0.3))
    return out


def _align_words(text: str, wav: Path, dur: float) -> list[tuple[float, float, str]]:
    """Gemini TTS returns audio only. Estimate word times: distribute words by length across the spoken region, and snap sentence
    boundaries to real pauses found by silence detection."""
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
    anchors_w, anchors_t = [0.0, total_w], [t0, t_end]
    used = set()
    for k, wd in enumerate(words[:-1]):
        if re.search(r"[.?!…]$", wd) and gaps[k] > 0:
            p_mid = wend[k] + gaps[k] / 2
            predicted = t0 + (t_end - t0) * p_mid / total_w
            best = None
            for si, (a, b) in enumerate(sil):
                mid = (a + b) / 2
                if si not in used and abs(mid - predicted) < 1.1 and (best is None or abs(mid - predicted) < abs(best[1] - predicted)):
                    best = (si, mid)
            if best:
                used.add(best[0])
                anchors_w.append(p_mid)
                anchors_t.append(best[1])
    order = np.argsort(anchors_w)
    aw, at = np.array(anchors_w)[order], np.array(anchors_t)[order]
    at = np.maximum.accumulate(at)                                   # keep time monotonic
    return [(float(np.interp(wstart[k], aw, at)), float(np.interp(wend[k], aw, at)), words[k]) for k in range(len(words))]


def _narrate_gemini(texts: list[str], workdir: Path, label: str) -> dict:
    import random
    rng = random.Random(label + "g")
    workdir.mkdir(parents=True, exist_ok=True)
    wavs, durations, starts, all_events = [], [], [], []
    t0, n = 0.0, len(texts)
    for i, text in enumerate(texts, 1):
        print(f"  [Voice:Gemini] {label} block {i}/{n} ({len(text.split())} words)")
        raw = workdir / f"{label}_{i:02d}_g24.wav"
        _gemini_wav(text, _mood(label, i, n), raw)
        conv = workdir / f"{label}_{i:02d}_conv.wav"
        subprocess.run([FFMPEG, "-y", "-i", str(raw), "-ac", "1", "-ar", str(SAMPLE_RATE), str(conv)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(str(conv), "rb") as w:
            dur_speech = w.getnframes() / w.getframerate()
        wpm = len(text.split()) / (dur_speech / 60)
        if wpm < 105:      # safety net: the model sometimes over-applies "slow"; keep the story moving (pitch is preserved)
            factor = min(1.45, 105 / wpm)
            fast = workdir / f"{label}_{i:02d}_fast.wav"
            subprocess.run([FFMPEG, "-y", "-i", str(conv), "-af", f"atempo={factor:.3f}", str(fast)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            conv = fast
            with wave.open(str(conv), "rb") as w:
                dur_speech = w.getnframes() / w.getframerate()
            print(f"    [pace] {wpm:.0f} wpm was too slow -> sped x{factor:.2f} ({len(text.split()) / (dur_speech / 60):.0f} wpm)")
        ev = _align_words(text, conv, dur_speech)
        tail = rng.uniform(0.7, 1.0)
        wav = workdir / f"{label}_{i:02d}.wav"
        with wave.open(str(conv), "rb") as wi, wave.open(str(wav), "wb") as wo:
            wo.setparams(wi.getparams())
            wo.writeframes(wi.readframes(wi.getnframes()))
            wo.writeframes(b"\x00\x00" * int(SAMPLE_RATE * tail))
        dur = dur_speech + tail
        starts.append(t0)
        durations.append(dur)
        all_events += [(t0 + s, t0 + e, w) for s, e, w in ev]
        wavs.append(wav)
        t0 += dur
    full = workdir / f"{label}_narration.wav"
    concat_wavs(wavs, full)
    return {"wav": full, "durations": durations, "starts": starts, "events": all_events, "total": t0}


def narrate(texts: list[str], workdir: Path, label: str, gap: float = 0.55) -> dict:
    """Expressive Gemini TTS first; if anything fails (quota, network) the WHOLE video falls back to Edge-TTS so the voice stays consistent."""
    from config import GEMINI_API_KEY, TTS_PROVIDER
    if TTS_PROVIDER == "gemini" and GEMINI_API_KEY:
        try:
            return _narrate_gemini(texts, workdir, label)
        except Exception as e:
            print(f"  [Voice] Gemini TTS unavailable ({str(e)[:140]}); falling back to Edge-TTS for this video")
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
