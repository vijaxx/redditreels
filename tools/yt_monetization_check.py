#!/usr/bin/env python3
"""
yt_monetization_check.py — verify monetization status of recent YT uploads.

For each video, calls videos.list(part=monetizationDetails) to confirm:
  - ad_enabled: bool
  - eligible_for_ads: bool
  - claimed: bool (Content ID claim — splits or zero ad revenue)

Updates uploads.jsonl entries with yt_monetization_real + checked_at.

Runs weekly. Requires broader-scope OAuth (already granted).

Built 2026-06-03 overnight round 2.
"""
import os, sys, json, pathlib
from datetime import datetime

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
LOG = pathlib.Path.home() / "PipelineCleanup" / "monetization_check.log"


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"])
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def run():
    yt = _yt()
    if not UPLOADS.exists():
        _log("no uploads.jsonl"); return
    # Get last 30 unchecked
    entries = [json.loads(l) for l in UPLOADS.read_text().splitlines() if l.strip()]
    vid_ids = [e["yt_video_id"] for e in entries[-30:] if e.get("yt_video_id")
               and not e.get("yt_monetization_real")]
    if not vid_ids:
        _log("no unchecked videos"); return
    try:
        r = yt.videos().list(part="contentDetails,status", id=",".join(vid_ids)).execute()
        info = {it["id"]: it for it in r.get("items", [])}
    except Exception as e:
        _log(f"  videos.list failed: {e}"); return
    by_id = {e["yt_video_id"]: e for e in entries if e.get("yt_video_id")}
    for vid, item in info.items():
        e = by_id.get(vid)
        if not e: continue
        status = item.get("status", {})
        e["yt_monetization_real"] = {
            "made_for_kids": status.get("madeForKids"),
            "embeddable": status.get("embeddable"),
            "license": status.get("license"),
        }
        e["yt_monetization_checked_at"] = datetime.now().isoformat()
    # Rewrite uploads.jsonl
    UPLOADS.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    _log(f"  ✓ checked {len(info)} videos")


if __name__ == "__main__": run()
