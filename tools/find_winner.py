#!/usr/bin/env python3
"""
find_winner.py — return the channel's current top-performing video URL.

Used by orchestrators to cross-promote a winner in every new upload's description.
Threshold: >100 views OR top video on the channel by views, whichever is higher.

Output: prints the URL to stdout, or empty string if no qualifying winner yet.
Cache the result for 1 hour to avoid quota burn.
"""
from __future__ import annotations
import json, os, pathlib, sys, time
from pathlib import Path

CACHE = pathlib.Path(os.path.expanduser("~/.winner_url_cache.json"))
CACHE_TTL_SEC = 3600  # 1 hour
MIN_VIEWS_THRESHOLD = 100
CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"  # FW (currently shared by all 3 pipelines)
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))


def _load_cached():
    if not CACHE.exists(): return None
    try:
        d = json.loads(CACHE.read_text())
        if time.time() - d.get("ts", 0) < CACHE_TTL_SEC:
            return d
    except Exception:
        pass
    return None


def _save_cache(url: str, views: int):
    CACHE.write_text(json.dumps({"ts": time.time(), "url": url, "views": views}))


def find_top_video():
    cached = _load_cached()
    if cached is not None:
        return cached["url"], cached["views"]

    cfg = json.loads(CREDS_PATH.read_text())
    rt = cfg.get("youtube_refresh_token_broad")
    if not rt:
        return "", 0  # no broad scope, can't query stats

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None, refresh_token=rt,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)

    # Get top 5 videos by view count
    s = yt.search().list(part="id", channelId=CHANNEL_ID, order="viewCount",
                         type="video", maxResults=5).execute()
    vid_ids = [it["id"]["videoId"] for it in s.get("items", [])]
    if not vid_ids:
        _save_cache("", 0); return "", 0
    stats = yt.videos().list(part="statistics", id=",".join(vid_ids)).execute()
    items = stats.get("items", [])
    if not items:
        _save_cache("", 0); return "", 0
    items.sort(key=lambda it: int(it.get("statistics", {}).get("viewCount", 0)), reverse=True)
    top = items[0]
    views = int(top.get("statistics", {}).get("viewCount", 0))
    if views < MIN_VIEWS_THRESHOLD:
        _save_cache("", views); return "", views
    url = f"https://youtube.com/shorts/{top['id']}"
    _save_cache(url, views)
    return url, views


def main():
    url, views = find_top_video()
    if url:
        print(url)
    else:
        print(f"# no winner yet (best is {views} views, threshold {MIN_VIEWS_THRESHOLD})", file=sys.stderr)


if __name__ == "__main__":
    main()
