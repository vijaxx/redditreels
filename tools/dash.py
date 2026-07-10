#!/usr/bin/env python3
"""
dash.py — single-command live dashboard for the RR-only setup.

Shows:
  - Current time + next 4 scheduled fires
  - Last 5 uploads (YT/FB/Rumble status per platform)
  - YT channel totals: subs, lifetime views, video count
  - View velocity for last 5 uploads
  - Active alerts (viral, failure streak)
  - Disk + Chrome health

Run anytime: ~/RedditReels/tools/dash.py
"""
import os, sys, json, subprocess, pathlib, urllib.request
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
UPLOADS = pathlib.Path.home() / "RedditReels/logs/uploads.jsonl"
PLINTH = pathlib.Path.home() / "PipelineCleanup"


def hdr(s):
    print(f"\n\033[1m=== {s} ===\033[0m")


def time_to_next_fire():
    fires = [(14, 0, "morning_batch"), (17, 30, "RR #1"),
             (18, 30, "RR #2"), (19, 30, "RR #3"), (20, 30, "RR #4")]
    now = datetime.now()
    upcoming = []
    for h, m, name in fires:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t < now: t = t + timedelta(days=1)
        delta_min = (t - now).total_seconds() / 60
        upcoming.append((delta_min, f"{h:02d}:{m:02d}", name))
    upcoming.sort()
    return upcoming


def yt_channel_stats():
    try:
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
        yt = build("youtube", "v3", credentials=creds)
        r = yt.channels().list(part="statistics", id=CHANNEL_ID).execute()
        s = r["items"][0]["statistics"]
        return {
            "subs": int(s.get("subscriberCount", 0)),
            "views": int(s.get("viewCount", 0)),
            "videos": int(s.get("videoCount", 0)),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def chrome_ok():
    try:
        urllib.request.urlopen("http://127.0.0.1:9223/json/version", timeout=2).read()
        return True
    except: return False


def cron_ok():
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True)
        return "com.redditreels.pipeline" in out and "com.pipelines.morningbatch" in out
    except: return False


def disk_free():
    try:
        out = subprocess.check_output(["df", "-h", str(pathlib.Path.home())], text=True)
        return out.strip().split("\n")[-1].split()[3]
    except: return "?"


def viral_alert_active():
    return (PLINTH / "VIRAL_ALERT.md").exists()


def failure_streak_active():
    return (PLINTH / "ALERT.md").exists()


def main():
    print(f"\033[1;36m╔══════ RR Dashboard — {datetime.now():%Y-%m-%d %a %H:%M:%S} ══════╗\033[0m")

    hdr("Health")
    print(f"  Chrome :9223   : {'ok' if chrome_ok() else 'down'}")
    print(f"  Launchd jobs   : {'ok' if cron_ok() else 'missing'}")
    print(f"  Disk free      : {disk_free()}")
    if viral_alert_active(): print("  VIRAL ALERT — read ~/PipelineCleanup/VIRAL_ALERT.md")
    if failure_streak_active(): print("  FAILURE STREAK — read ~/PipelineCleanup/ALERT.md")

    hdr("Next fires")
    for delta_min, t, name in time_to_next_fire()[:4]:
        h = int(delta_min // 60); m = int(delta_min % 60)
        print(f"  {t}  in {h}h {m:02d}m  ({name})")

    hdr("YT channel totals")
    s = yt_channel_stats()
    if "error" in s:
        print(f"  {s['error']}")
    else:
        print(f"  Subscribers : {s['subs']:>6}")
        print(f"  Lifetime views : {s['views']:>4}")
        print(f"  Total videos : {s['videos']:>5}")

    hdr("Last 5 uploads")
    if UPLOADS.exists():
        lines = UPLOADS.read_text().splitlines()[-5:]
        for line in lines:
            try:
                d = json.loads(line)
                yt_ok = "y" if d.get("yt_video_id") else "-"
                fb_ok = "y" if d.get("fb_posted") else "-"
                ru_ok = "y" if d.get("rumble_url") else "-"
                bait = d.get("results", {}).get("fb_bait_pinned", "-")
                print(f"  {d.get('ts','?')}  YT={yt_ok} FB={fb_ok} RUM={ru_ok}  bait={bait}  {(d.get('title') or '')[:48]}")
            except: pass

    hdr("View velocity (last 5)")
    vhist = PLINTH / "view_history.jsonl"
    if vhist.exists():
        seen = {}
        for line in vhist.read_text().splitlines():
            try:
                d = json.loads(line)
                seen[d["video_id"]] = d  # last wins
            except: pass
        for vid, d in list(seen.items())[-5:]:
            print(f"  {vid}  {d['views']:>3}v  {d['age_hours']:.0f}h  {d['vph']:.1f}vph")
    else:
        print("  (no history yet — run view_velocity.py to populate)")

    print(f"\n\033[1;36m╚══════════════════════════════════════════════════════════╝\033[0m")


if __name__ == "__main__":
    main()
