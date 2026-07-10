#!/usr/bin/env python3
"""
rotate_trailer.py — find top-performing video by views, set as channel trailer.

The "unsubscribedTrailer" is what plays when a new visitor lands on the channel
page. Setting it to the best-performing video maximizes the first-impression
sub-conversion rate.

Runs weekly (Sundays via morning_batch / weekly_learn step).
"""
from __future__ import annotations
import json, os, pathlib, sys
from datetime import datetime

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
LOG_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/rotate_trailer.log"))


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS_PATH.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"=== {datetime.now().isoformat()} rotate_trailer ==="]
    try:
        yt = _yt()
        # Top 5 by views
        s = yt.search().list(part="id,snippet", channelId=CHANNEL_ID,
                             order="viewCount", type="video", maxResults=5).execute()
        vid_ids = [it["id"]["videoId"] for it in s.get("items", [])]
        if not vid_ids:
            log_lines.append("  no videos found"); return
        stats = yt.videos().list(part="statistics,snippet", id=",".join(vid_ids)).execute()
        items = stats.get("items", [])
        items.sort(key=lambda it: int(it.get("statistics", {}).get("viewCount", 0)), reverse=True)
        top = items[0]
        top_id = top["id"]
        top_views = int(top.get("statistics", {}).get("viewCount", 0))
        top_title = top["snippet"]["title"][:80]
        log_lines.append(f"  top video: {top_id} ({top_views} views) — '{top_title}'")

        # Current trailer?
        ch = yt.channels().list(part="brandingSettings", id=CHANNEL_ID).execute()
        current = ch["items"][0].get("brandingSettings", {}).get("channel", {}).get("unsubscribedTrailer", "")
        log_lines.append(f"  current trailer: {current!r}")
        if current == top_id:
            log_lines.append(f"   already set — no change needed")
        else:
            # Update
            yt.channels().update(part="brandingSettings", body={
                "id": CHANNEL_ID,
                "brandingSettings": {
                    "channel": {**ch["items"][0].get("brandingSettings", {}).get("channel", {}),
                                "unsubscribedTrailer": top_id}
                }
            }).execute()
            log_lines.append(f"   TRAILER UPDATED: {current!r} → {top_id}")
    except Exception as e:
        log_lines.append(f"  FAIL: {e}")

    with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")
    print("\n".join(log_lines[-5:]))


if __name__ == "__main__":
    main()
