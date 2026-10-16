"""Audio-only sample: natural sentence-by-sentence voice + room reverb + quiet ambience. ~1 minute to run, no video render.
   python tests/voice_sample.py      ->  output/voice_samples/sample_natural.mp3
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import OUTPUT_DIR  # noqa: E402
from modules import tts, video  # noqa: E402

TEXT = [
    "My grandfather ran a ferry on the Kalu Ganga for forty years. He never once crossed after midnight. "
    "When I asked him why, he only said... the river changes its mind at night.",
    "One monsoon evening, a man in a white sarong stood on the far bank. He raised one hand, very slowly. "
    "Grandfather looked at the water. Then at the man. And untied the boat anyway.",
    "Halfway across, the lantern went out. There was no wind. The boat grew heavier with every stroke of the oar, "
    "as if somebody else had quietly climbed aboard. Who was sitting behind him?",
]

if __name__ == "__main__":
    out = OUTPUT_DIR / "voice_samples"
    out.mkdir(parents=True, exist_ok=True)
    nar = tts.narrate(TEXT, out / "work", "sample")
    mp3 = video.audio_only(nar["wav"], nar["total"], 12345, nar["starts"], out / "work", out / "sample_natural.mp3")
    ev = nar["events"]
    mono = all(ev[k][0] <= ev[k + 1][0] + 1e-6 for k in range(len(ev) - 1))
    print("caption events:", len(ev), "| monotonic:", mono, "| first:", [(round(s, 2), w) for s, _, w in ev[:6]],
          "| last end %.1fs of %.1fs" % (ev[-1][1], nar["total"]))
    print("Listen:", mp3.resolve(), f"({nar['total']:.0f}s)")
