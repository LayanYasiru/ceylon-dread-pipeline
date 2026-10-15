"""Offline render test: uses the images already in legacy outputs + real Edge-TTS voice. No LLM / image API needed.
Run:  python tests/smoke_render.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import OUTPUT_DIR  # noqa: E402
from modules import images, produce  # noqa: E402

pool = sorted((OUTPUT_DIR / "images").glob("kandy_master_*.jpg"))
assert pool, "no local sample images found in output/images"


def fake_generate_many(prompts, folder, prefix, size, base_seed):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    outs = []
    for i, _ in enumerate(prompts):
        src = Image.open(pool[i % len(pool)]).convert("RGB")
        out = folder / f"{prefix}_{i + 1:02d}.jpg"
        tw, th = size
        s = max(tw / src.width, th / src.height)
        src = src.resize((int(src.width * s) + 1, int(src.height * s) + 1))
        l, t = (src.width - tw) // 2, (src.height - th) // 2
        src.crop((l, t, l + tw, t + th)).save(out, "JPEG", quality=92)
        outs.append(out)
    return outs


images.generate_many = fake_generate_many

story = {"id": "smoke_test", "title": "Smoke test"}
script = {
    "title": "The Boatman Who Never Asked for Money",
    "thumbnail_text": "DO NOT CROSS",
    "description": "test", "fiction_note": "fiction", "tags": ["test"],
    "scenes": [
        {"narration": "My grandfather ran a ferry on the Kalu Ganga for forty years. He never once crossed after midnight. "
                      "When I asked him why, he only said, the river changes its mind at night.",
         "visuals": ["a", "b"]},
        {"narration": "One monsoon evening a man in a white sarong stood on the far bank. He raised one hand, very slowly. "
                      "Grandfather looked at the water, then at the man, and untied the boat anyway.",
         "visuals": ["c", "d"]},
        {"narration": "Halfway across, the lantern went out. There was no wind. The boat grew heavier with every stroke of the oar, "
                      "as if somebody else had quietly climbed aboard.",
         "visuals": ["e", "f"]},
        {"narration": "On the far bank there was no village. Only jungle. The man stepped off without a sound, and the water behind him was perfectly dry.",
         "visuals": ["g", "h"]},
        {"narration": "Grandfather never crossed after midnight again. And the oar? It still hangs in our house, and on rainy nights, it is wet.",
         "visuals": ["i", "j"]},
    ],
    "shorts": [{"title": "The Boatman", "visuals": ["1", "2", "3", "4", "5"],
                "narration": "My grandfather ran a ferry for forty years. He never crossed after midnight. One night a man in white raised his hand. "
                             "Halfway across, the lantern died, and the boat grew heavier. On the far bank there was no village. The full story is on our channel."}],
}
if __name__ == "__main__":   # required: parallel render workers re-import this module on Windows
    res = produce.produce_episode(story, script, "smoke")
    print("DONE", res["long"], res["thumb"], [s["path"] for s in res["shorts"]])
