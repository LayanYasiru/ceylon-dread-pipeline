"""Publishing to YouTube (long + Shorts), Facebook (Page video + Reels) and TikTok (inbox draft).
Every function returns a dict and never raises, so one failing platform cannot kill the daily run.
Tokens come from env / token.json (see SETUP.md). Nothing is hardcoded.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from config import (FACEBOOK_ACCESS_TOKEN, FACEBOOK_PAGE_ID, PUBLISH_MODE, PUBLISH_SLOTS_UTC,
                    TIKTOK_ACCESS_TOKEN, YOUTUBE_TOKEN_FILE, YOUTUBE_TOKEN_JSON, CHANNEL_NAME)

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
AI_NOTE = "Visuals are AI-generated and the narration is a synthetic voice."


# ---------------- scheduling ----------------
def next_slots(n: int) -> list[datetime]:
    """Next n publish times (UTC) taken from PUBLISH_SLOTS_UTC, at least 70 minutes from now."""
    now = datetime.now(timezone.utc) + timedelta(minutes=70)
    slots = []
    day = now.date()
    while len(slots) < n:
        for hhmm in PUBLISH_SLOTS_UTC:
            h, m = map(int, hhmm.split(":"))
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
            if t >= now and len(slots) < n:
                slots.append(t)
        day += timedelta(days=1)
    return slots


# ---------------- YouTube ----------------
def _yt_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    raw = YOUTUBE_TOKEN_JSON or (YOUTUBE_TOKEN_FILE.read_text(encoding="utf-8") if YOUTUBE_TOKEN_FILE.exists() else "")
    if not raw:
        raise RuntimeError("No YouTube token. Run `python setup_youtube_auth.py` once (see SETUP.md).")
    creds = Credentials.from_authorized_user_info(json.loads(raw), YT_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_youtube(video: Path, title: str, description: str, tags: list[str], publish_at: datetime | None = None,
                   thumbnail: Path | None = None, is_short: bool = False) -> dict:
    try:
        from googleapiclient.http import MediaFileUpload

        yt = _yt_service()
        if is_short and "#shorts" not in (title + description).lower():
            description += "\n\n#Shorts"
        status = {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
        if PUBLISH_MODE == "public":
            status["privacyStatus"] = "public"
        elif PUBLISH_MODE == "scheduled" and publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        else:
            status["privacyStatus"] = "private"
        body = {
            "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15], "categoryId": "24",
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status,
        }
        media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        retries = 0
        while resp is None:
            try:
                _, resp = req.next_chunk()
            except Exception as e:      # transient network errors on big uploads
                retries += 1
                if retries > 5:
                    raise
                print(f"  [YouTube chunk retry {retries}] {str(e)[:100]}")
                time.sleep(5 * retries)
        vid = resp["id"]
        out = {"status": "ok", "id": vid, "url": f"https://youtu.be/{vid}", "privacy": status["privacyStatus"],
               "publishAt": status.get("publishAt")}
        if thumbnail and thumbnail.exists():
            try:
                yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail))).execute()
            except Exception as e:
                out["thumbnail_error"] = str(e)[:160]   # needs a phone-verified channel
        print(f"[YouTube] {out['url']} ({out['privacy']}{' @ ' + out['publishAt'] if out.get('publishAt') else ''})")
        return out
    except Exception as e:
        print(f"[YouTube ERROR] {type(e).__name__}: {str(e)[:300]}")
        return {"status": "error", "message": str(e)[:300]}


# ---------------- Facebook ----------------
def _fb_ready() -> bool:
    return bool(FACEBOOK_PAGE_ID and FACEBOOK_ACCESS_TOKEN)


def upload_facebook_video(video: Path, title: str, description: str, thumbnail: Path | None = None,
                          publish_at: datetime | None = None) -> dict:
    if not _fb_ready():
        return {"status": "skipped", "message": "FACEBOOK_PAGE_ID / FACEBOOK_ACCESS_TOKEN not set"}
    try:
        data = {"title": title[:100], "description": description[:5000], "access_token": FACEBOOK_ACCESS_TOKEN}
        if PUBLISH_MODE == "scheduled" and publish_at:
            data.update({"published": "false", "scheduled_publish_time": str(int(publish_at.timestamp()))})
        elif PUBLISH_MODE == "private":
            data["published"] = "false"
        files = {"source": open(video, "rb")}
        if thumbnail and thumbnail.exists():
            files["thumb"] = open(thumbnail, "rb")
        r = requests.post(f"https://graph-video.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/videos", data=data, files=files, timeout=1800)
        for f in files.values():
            f.close()
        j = r.json()
        if "id" in j:
            print(f"[Facebook video] id={j['id']}")
            return {"status": "ok", "id": j["id"]}
        raise RuntimeError(json.dumps(j)[:300])
    except Exception as e:
        print(f"[Facebook video ERROR] {str(e)[:300]}")
        return {"status": "error", "message": str(e)[:300]}


def upload_facebook_reel(video: Path, description: str, publish_at: datetime | None = None) -> dict:
    if not _fb_ready():
        return {"status": "skipped", "message": "FACEBOOK_PAGE_ID / FACEBOOK_ACCESS_TOKEN not set"}
    try:
        base = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/video_reels"
        start = requests.post(base, data={"upload_phase": "start", "access_token": FACEBOOK_ACCESS_TOKEN}, timeout=60).json()
        if "video_id" not in start:
            raise RuntimeError(json.dumps(start)[:300])
        size = video.stat().st_size
        with open(video, "rb") as f:
            up = requests.post(start["upload_url"], data=f,
                               headers={"Authorization": f"OAuth {FACEBOOK_ACCESS_TOKEN}", "offset": "0", "file_size": str(size)}, timeout=900)
        if up.status_code != 200:
            raise RuntimeError(f"upload phase: {up.text[:200]}")
        fin = {"upload_phase": "finish", "video_id": start["video_id"], "description": description[:2000],
               "access_token": FACEBOOK_ACCESS_TOKEN}
        if PUBLISH_MODE == "scheduled" and publish_at:
            fin.update({"video_state": "SCHEDULED", "scheduled_publish_time": str(int(publish_at.timestamp()))})
        elif PUBLISH_MODE == "private":
            fin["video_state"] = "DRAFT"
        else:
            fin["video_state"] = "PUBLISHED"
        j = requests.post(base, data=fin, timeout=120).json()
        if j.get("success") or "post_id" in j:
            print(f"[Facebook reel] video_id={start['video_id']}")
            return {"status": "ok", "id": start["video_id"]}
        raise RuntimeError(json.dumps(j)[:300])
    except Exception as e:
        print(f"[Facebook reel ERROR] {str(e)[:300]}")
        return {"status": "error", "message": str(e)[:300]}


# ---------------- TikTok ----------------
def upload_tiktok_draft(video: Path, title: str) -> dict:
    """Sends the video to the creator's TikTok inbox as a DRAFT (you tap Post). Direct auto-posting needs TikTok's app audit."""
    if not TIKTOK_ACCESS_TOKEN:
        return {"status": "skipped", "message": "TIKTOK_ACCESS_TOKEN not set - post the file from output/ready manually"}
    try:
        size = video.stat().st_size
        init = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
            headers={"Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}", "Content-Type": "application/json; charset=UTF-8"},
            json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}, timeout=60,
        ).json()
        url = init.get("data", {}).get("upload_url")
        if not url:
            raise RuntimeError(json.dumps(init)[:300])
        with open(video, "rb") as f:
            up = requests.put(url, data=f, headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                                                    "Content-Range": f"bytes 0-{size - 1}/{size}"}, timeout=600)
        if up.status_code not in (200, 201):
            raise RuntimeError(f"upload: {up.status_code} {up.text[:150]}")
        print("[TikTok] draft sent to your TikTok inbox")
        return {"status": "ok", "publish_id": init["data"].get("publish_id")}
    except Exception as e:
        print(f"[TikTok ERROR] {str(e)[:300]}")
        return {"status": "error", "message": str(e)[:300]}


# ---------------- text helpers ----------------
def long_description(meta: dict) -> str:
    return (f"{meta['description']}\n\n{meta.get('fiction_note', '')}\n{AI_NOTE}\n\n"
            f"Subscribe to {CHANNEL_NAME} for a new Sri Lankan horror story every day.\n\n"
            + " ".join("#" + t.replace(" ", "") for t in meta.get("tags", [])[:6]) + " #SriLanka #Horror")


def short_description(meta: dict, short: dict) -> str:
    return (f"{short['title']}\n\nFull story on the channel: {meta['title']}\n{AI_NOTE}\n\n#SriLanka #Horror #ScaryStories #Shorts")
