"""Daily automation entry point.

  python run_daily.py                      # 2 stories -> 2 long videos + 4 shorts, publish everywhere
  python run_daily.py --no-publish         # produce only (review output/ready first)
  python run_daily.py --story-id kuveni_curse --count 1
  python run_daily.py --story-id X --publish-only   # retry uploads for an already produced episode
"""
import argparse
import json
import shutil
import sys
import traceback
from datetime import timedelta

import config
from modules import publisher, story_bank
from modules.produce import produce_episode
from modules.script_writer import load_script_file, write_script


def publish_episode(meta: dict, slot, shorts_offsets_h=(2, 5)) -> dict:
    from pathlib import Path
    res = {"long": {}, "shorts": []}
    thumb = Path(meta["thumbnail"])
    desc = publisher.long_description(meta)
    res["long"]["youtube"] = publisher.upload_youtube(Path(meta["long"]), meta["title"], desc, meta["tags"], slot, thumb)
    res["long"]["facebook"] = publisher.upload_facebook_video(Path(meta["long"]), meta["title"], desc, thumb, slot)
    for j, sh in enumerate(meta["shorts"]):
        t = slot + timedelta(hours=shorts_offsets_h[min(j, len(shorts_offsets_h) - 1)])
        sdesc = publisher.short_description(meta, sh)
        entry = {
            "youtube": publisher.upload_youtube(Path(sh["path"]), f"{sh['title']} #Shorts", sdesc, meta["tags"], t, is_short=True),
            "facebook": publisher.upload_facebook_reel(Path(sh["path"]), sdesc, t),
            "tiktok": publisher.upload_tiktok_draft(Path(sh["path"]), sh["title"]),
        }
        res["shorts"].append(entry)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=config.VIDEOS_PER_RUN)
    ap.add_argument("--story-id")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--publish-only", action="store_true")
    ap.add_argument("--script-file", help="use a hand-written/edited script JSON instead of the LLM")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    stories = story_bank.pick_next(args.count, args.story_id)
    slots = publisher.next_slots(len(stories))
    print(f"Plan: {[s['id'] for s in stories]}  publish mode={config.PUBLISH_MODE}  slots={[s.isoformat() for s in slots]}")

    ok = 0
    for story, slot in zip(stories, slots):
        sid = story["id"]
        try:
            if args.publish_only:
                meta = json.loads((config.READY_DIR / sid / "metadata.json").read_text(encoding="utf-8"))
            else:
                script = load_script_file(args.script_file) if args.script_file else write_script(story)
                meta = produce_episode(story, script, slot.isoformat())["meta"]
            result = {} if args.no_publish else publish_episode(meta, slot)
            if not args.no_publish:   # a dry run must not use up the story
                story_bank.mark_done(sid, {"title": meta["title"], "publish": result})
            if not args.keep_work:
                shutil.rmtree(config.WORK_DIR / sid, ignore_errors=True)
            ok += 1
        except Exception:
            print(f"[FAILED] {sid}\n{traceback.format_exc()}")
    print(f"Done: {ok}/{len(stories)} episodes.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
