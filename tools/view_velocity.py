#!/usr/bin/env python3
"""Tracks views/hour for anything uploaded in the last 48 hours, hourly.
Past a threshold it's flagged as unusually hot in a local alert file, so a
human -- or a follow-up script -- can react: pin a comment, cross-post, plan
a part two."""
import os, sys, json, pathlib
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
HISTORY = pathlib.Path.home() / "PipelineCleanup" / "view_history.jsonl"
ALERT = pathlib.Path.home() / "PipelineCleanup" / "VIRAL_ALERT.md"
LOG = pathlib.Path.home() / "PipelineCleanup" / "view_velocity.log"

VIRAL_VPH_THRESHOLD = 30  # >30 views/hour = unusual, alert
MAX_AGE_HOURS = 48


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


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _last_views_for(vid: str) -> dict:
    """Return last recorded view count + timestamp for this video."""
    if not HISTORY.exists(): return {}
    last = None
    for line in HISTORY.read_text().splitlines():
        try:
            d = json.loads(line)
            if d.get("video_id") == vid:
                last = d
        except: continue
    return last or {}


def _record(vid: str, views: int, age_h: float, vph: float):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now().isoformat(),
            "video_id": vid, "views": views,
            "age_hours": age_h, "vph": round(vph, 2)
        }) + "\n")


def _write_alert(events: list):
    """Write the VIRAL_ALERT.md so user sees it on next dashboard check."""
    lines = ["#  VIRAL ALERT ", "",
             f"Generated: {datetime.now().isoformat()}",
             "",
             "Videos exceeding viral velocity threshold:",
             ""]
    for e in events:
        lines.append(f"- **{e['title'][:60]}** — {e['views']} views in {e['age_h']:.0f}h "
                     f"({e['vph']:.1f} views/hour)")
        lines.append(f"  - YT: https://youtube.com/shorts/{e['vid']}")
        lines.append(f"  - Suggested action: {e['action']}")
        lines.append("")
    ALERT.write_text("\n".join(lines))


def run():
    yt = _yt()
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails,snippet",
                                   playlistId=uploads, maxResults=30).execute()
    now = datetime.now(timezone.utc)
    candidates = []
    for it in pl.get("items", []):
        vid = it["contentDetails"]["videoId"]
        pub = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
        age_h = (now - pub).total_seconds() / 3600
        if 1 <= age_h <= MAX_AGE_HOURS:
            candidates.append((vid, age_h, it["snippet"]["title"]))
    if not candidates:
        _log("no candidates in age window"); return

    stats = yt.videos().list(part="statistics", id=",".join(c[0] for c in candidates)).execute()
    views_by = {it["id"]: int(it["statistics"].get("viewCount", 0)) for it in stats.get("items", [])}
    viral = []
    for vid, age_h, title in candidates:
        views = views_by.get(vid, 0)
        vph = views / max(age_h, 0.1)
        _record(vid, views, age_h, vph)
        last = _last_views_for(vid)
        delta = views - last.get("views", 0) if last else 0
        if vph >= VIRAL_VPH_THRESHOLD:
            action = ("Pin a follow-up comment + boost the FB version. "
                      "If 100+ views, queue a Part 2.")
            viral.append({"vid": vid, "views": views, "age_h": age_h,
                          "vph": vph, "title": title, "action": action})
            _log(f" VIRAL: {vid} ({views}v in {age_h:.0f}h = {vph:.1f}vph)  {title[:50]}")
        else:
            _log(f"  {vid}  {views}v  {age_h:.0f}h  {vph:.1f}vph  Δ+{delta}")

    if viral:
        _write_alert(viral)
        _log(f"=== {len(viral)} VIRAL alerts written to {ALERT} ===")
        # Push notification — Telegram + macOS native
        try:
            import sys as _sn
            _sn.path.insert(0, str(pathlib.Path.home() / "RedditReels/tools"))
            from notify import notify
            for v in viral:
                notify(f" VIRAL: {v['title'][:60]}",
                        f"{v['views']} views in {v['age_h']:.0f}h ({v['vph']:.1f} vph)\n"
                        f"https://youtube.com/shorts/{v['vid']}")
        except Exception: pass
    else:
        if ALERT.exists():
            ALERT.unlink()  # clear old alerts
        _log("=== no viral videos this cycle ===")


if __name__ == "__main__": run()
