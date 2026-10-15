"""Turns one script into: 1 long video + N vertical shorts + thumbnail + captions + metadata."""
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from config import ANIMATION, LONG_SIZE, SHORT_SIZE, WORK_DIR, READY_DIR, CHANNEL_NAME
from modules import images, parallax, tts, video
from modules.thumbnail import make_thumbnail

SHORT_IMAGES = 5


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:50]


def _render(label: str, size, texts: list[str], visual_paths: list[Path], visuals_per_scene: list[int],
            work: Path, out: Path, seed: int, caption_words: int, upper: bool, fade: float) -> dict:
    nar = tts.narrate(texts, work, label)
    timeline = video.build_timeline(nar["starts"], nar["durations"], visuals_per_scene, nar["events"])
    assert len(timeline) == len(visual_paths), (len(timeline), len(visual_paths))
    segs = []
    jobs = [(k, dur, img, work / f"{label}_seg_{k:02d}.mp4") for k, ((_, dur), img) in enumerate(zip(timeline, visual_paths))]
    if ANIMATION == "parallax":
        print(f"  [Render] {label}: depth maps for {len(jobs)} shots...")
        depths = {str(img): str(parallax.depth_for(img)) for _, _, img, _ in jobs}
        workers = max(1, min(os.cpu_count() or 2, 4))
        print(f"  [Render] {label}: animating {len(jobs)} shots with {workers} workers...")
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(parallax.render_parallax, str(img), depths[str(img)], dur, size, k, str(seg), fade, seed)
                    for k, dur, img, seg in jobs]
            segs = [Path(f.result()) for f in futs]
    else:
        for k, dur, img, seg in jobs:
            print(f"  [Render] {label} shot {k + 1}/{len(jobs)} ({dur:.1f}s)")
            segs.append(video.render_segment(img, dur, size, k, seg, fade))
    ass = tts.build_ass(nar["events"], size, work / f"{label}.ass", caption_words, upper)
    print(f"  [Render] {label}: mixing audio + captions ({nar['total']:.0f}s)...")
    video.final_mix_and_captions(segs, nar["wav"], ass, nar["total"], seed, nar["starts"], out, work)
    return {"path": out, "seconds": nar["total"], "ass": ass}


def produce_episode(story: dict, script: dict, slot_tag: str) -> dict:
    sid = story["id"]
    work = WORK_DIR / sid
    outdir = READY_DIR / sid
    work.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    seed = video.seed_from(sid)

    # ---------- long video ----------
    scenes = script["scenes"]
    per_scene = [min(2, len(s["visuals"])) for s in scenes]
    prompts = [v for s in scenes for v in s["visuals"][:2]]
    print(f"[Produce] {sid}: generating {len(prompts)} long-form images...")
    long_imgs = images.generate_many(prompts, work / "img_long", "L", LONG_SIZE, seed)
    long_out = outdir / f"{sid}_long.mp4"
    long = _render("long", LONG_SIZE, [s["narration"] for s in scenes], long_imgs, per_scene, work, long_out,
                   seed, caption_words=6, upper=False, fade=0.4)

    thumb = make_thumbnail(long_imgs[min(2, len(long_imgs) - 1)], script.get("thumbnail_text") or script["title"], outdir / f"{sid}_thumb.jpg")

    # ---------- shorts ----------
    shorts = []
    for j, sh in enumerate(script["shorts"], 1):
        prompts_v = sh["visuals"][:SHORT_IMAGES]
        print(f"[Produce] {sid} short {j}: generating {len(prompts_v)} vertical images...")
        imgs = images.generate_many(prompts_v, work / f"img_short{j}", f"S{j}", SHORT_SIZE, seed + 1000 * j)
        out = outdir / f"{sid}_short{j}.mp4"
        r = _render(f"short{j}", SHORT_SIZE, [sh["narration"]], imgs, [len(imgs)], work, out,
                    seed + j, caption_words=3, upper=True, fade=0.2)
        shorts.append({"path": r["path"], "seconds": r["seconds"], "title": sh["title"]})

    meta = {
        "story_id": sid, "slot": slot_tag, "title": script["title"], "description": script["description"],
        "fiction_note": script.get("fiction_note", ""), "tags": script.get("tags", []),
        "long": str(long["path"]), "long_seconds": long["seconds"], "thumbnail": str(thumb),
        "shorts": [{"path": str(s["path"]), "title": s["title"], "seconds": s["seconds"]} for s in shorts],
    }
    (outdir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"meta": meta, "long": long["path"], "thumb": thumb, "shorts": shorts}
