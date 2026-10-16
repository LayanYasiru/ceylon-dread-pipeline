"""Try Gemini TTS (free API key) with storyteller direction: whisper, slow-downs, tension.
   python tests/gemini_tts_test.py   ->  output/voice_samples/gemini_*.wav
"""
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

OUT = config.OUTPUT_DIR / "voice_samples"
OUT.mkdir(parents=True, exist_ok=True)

DIRECTION = (
    "You are an old Sri Lankan storyteller telling a ghost story by lamplight to a small circle of listeners, late at night. "
    "Speak slowly and intimately, in a low calm voice with a warm, slightly gravelly tone. Let the dread build gradually. "
    "Slow down on the frightening moments and leave long pauses. Drop to a whisper for the lines in the middle of the story that "
    "reveal the supernatural, and whisper the final line. Do not rush. Sound like a real person remembering something they wish they could forget."
)
TEXT = (
    "My grandfather ran a ferry on the Kalu Ganga for forty years. He never once crossed after midnight. "
    "When I asked him why, he only said... the river changes its mind at night.\n\n"
    "One monsoon evening, a man in a white sarong stood on the far bank. He raised one hand, very slowly. "
    "Grandfather looked at the water. Then at the man. And untied the boat anyway.\n\n"
    "Halfway across, the lantern went out. There was no wind. The boat grew heavier with every stroke of the oar, "
    "as if somebody else had quietly climbed aboard.\n\n"
    "And the oar? It still hangs in our house. And on rainy nights... it is wet."
)


def synth(model: str, voice: str, name: str) -> bool:
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    t = time.time()
    try:
        r = client.models.generate_content(
            model=model,
            contents=f"{DIRECTION}\n\nTranscript:\n{TEXT}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            ),
        )
        pcm = r.candidates[0].content.parts[0].inline_data.data
        path = OUT / f"gemini_{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        print(f"OK  {model} / {voice}: {len(pcm) / 48000:.0f}s audio in {time.time() - t:.0f}s -> {path.name}")
        return True
    except Exception as e:
        print(f"ERR {model} / {voice}: {type(e).__name__} {str(e)[:220]}")
        return False


if __name__ == "__main__":
    for model, voice, name in [
        ("gemini-3.8-flash-tts", "Charon", "g38_charon"),
        ("gemini-3.8-flash-tts", "Algenib", "g38_algenib"),
        ("gemini-2.5-flash-preview-tts", "Enceladus", "g25_enceladus"),
    ]:
        synth(model, voice, name)
