#!/usr/bin/env python3
"""
dashboard.py — weekly health/revenue snapshot across FW + SS + RR.

Reads each pipeline's logs + queries YT Data API for channel-level stats.
Prints a single-screen actionable dashboard:
  - YPP progress (subs / watch hours / Shorts views thresholds)
  - Last 7 days: posts per pipeline + view counts + best/worst performer
  - Ad-safety distribution (green/yellow/red) — RR only, from estimator
  - Cron health (last fire success per pipeline)
  - Top recommendation for the week

Usage:
  python3 ~/RedditReels/tools/dashboard.py             # one-shot print
  python3 ~/RedditReels/tools/dashboard.py --json      # JSON for programs

Add a weekly cron via the cleanup LaunchAgent if you want auto-snapshot.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys
from datetime import datetime, timedelta, timezone

HOME = pathlib.Path.home()
PIPELINES = {
    "RedditReels": {"root": HOME / "RedditReels",
                    "log":  HOME / "RedditReels/logs/pipeline.log",
                    "label": "RR"},
}

# YPP thresholds (YouTube Partner Program — Shorts path)
YPP_SUBS_TARGET = 1000
YPP_SHORTS_VIEWS_TARGET = 10_000_000   # in trailing 90 days
YPP_WATCH_HOURS_TARGET = 4000          # long-form path (alt)


def _load_creds():
    p = HOME / "RedditReels/config/credentials.json"
    return json.loads(p.read_text())


def _yt_client(cfg):
    """Build YT client. Picks scope set matching whichever refresh token is present."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    # Prefer broad if available; fall back to upload-only (which still lets us
    # call channels.list mine=True and videos.list id=... — limited but useful)
    if cfg.get("youtube_refresh_token_broad"):
        rt = cfg["youtube_refresh_token_broad"]
        scopes = ["https://www.googleapis.com/auth/youtube",
                  "https://www.googleapis.com/auth/youtube.force-ssl"]
    else:
        rt = cfg["youtube_refresh_token"]
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = Credentials(
        token=None, refresh_token=rt,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"],
        client_secret=cfg["youtube_client_secret"],
        scopes=scopes,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def fetch_channel_stats() -> dict:
    """Return {subs, total_views, video_count} for `mine`."""
    try:
        cfg = _load_creds()
        # mine=True works with upload scope too (returns the channel that owns the token)
        yt = _yt_client(cfg)
        resp = yt.channels().list(part="statistics,snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            return {"error": "no channel returned (token may not be channel-bound)"}
        ch = items[0]
        st = ch.get("statistics", {})
        return {
            "channel_id": ch["id"],
            "channel_title": ch.get("snippet", {}).get("title", ""),
            "subs": int(st.get("subscriberCount", 0)),
            "total_views": int(st.get("viewCount", 0)),
            "video_count": int(st.get("videoCount", 0)),
            "hidden_subs": st.get("hiddenSubscriberCount", False),
        }
    except Exception as e:
        return {"error": f"{e.__class__.__name__}: {e}"}


def count_recent_uploads(log_path: pathlib.Path, days: int = 7) -> dict:
    """Scan a pipeline log for upload outcomes in last N days."""
    if not log_path.exists():
        return {"posts": 0, "success_yt": 0, "fail_yt": 0, "errors": 0, "log_missing": True}
    cutoff = datetime.now() - timedelta(days=days)
    out = {"posts": 0, "success_yt": 0, "fail_yt": 0, "errors": 0}
    yt_re = re.compile(r"youtube\.com/(shorts|watch)/")
    yt_fail_re = re.compile(r"YouTube upload failed", re.IGNORECASE)
    err_re = re.compile(r"\b(ERROR|FATAL|Traceback)\b")
    ts_re = re.compile(r"^(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})")
    try:
        with open(log_path) as f:
            for line in f:
                m = ts_re.match(line)
                if m:
                    try: lts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                    except: continue
                    if lts < cutoff: continue
                if yt_re.search(line): out["success_yt"] += 1
                if yt_fail_re.search(line): out["fail_yt"] += 1
                if err_re.search(line): out["errors"] += 1
        out["posts"] = out["success_yt"] + out["fail_yt"]
    except Exception as e:
        out["scan_error"] = str(e)
    return out


def read_uploads_jsonl(path: pathlib.Path, days: int = 7) -> list:
    """Read RR uploads.jsonl entries from last N days."""
    if not path.exists(): return []
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for line in path.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        try:
            ts = datetime.strptime(e.get("ts","")[:13], "%Y%m%d_%H%M%S"[:13])
        except Exception:
            ts = None
        if ts and ts < cutoff: continue
        out.append(e)
    return out


def ad_safety_distribution(entries: list) -> dict:
    """Tally green/yellow/red from RR uploads.jsonl."""
    counts = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    for e in entries:
        est = (e.get("ad_safe_estimate") or {}).get("ad_safe") or "unknown"
        counts[est] = counts.get(est, 0) + 1
    return counts


def fetch_yt_video_views(video_ids: list) -> dict:
    """Return {video_id: view_count}."""
    if not video_ids: return {}
    try:
        cfg = _load_creds()
        yt = _yt_client(cfg)
        out = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
            resp = yt.videos().list(part="statistics", id=",".join(chunk)).execute()
            for it in resp.get("items", []):
                out[it["id"]] = int(it.get("statistics", {}).get("viewCount", 0))
        return out
    except Exception:
        return {}


# ---------- pretty printer ----------

def hr(): print("─" * 72)


def print_dashboard(want_json: bool):
    now = datetime.now()
    channel = fetch_channel_stats()

    per_pipeline = {}
    for name, info in PIPELINES.items():
        per_pipeline[name] = count_recent_uploads(info["log"], days=7)

    rr_uploads = read_uploads_jsonl(HOME / "RedditReels/logs/uploads.jsonl", days=7)
    rr_safety = ad_safety_distribution(rr_uploads)

    # Per-video view counts for last 5 RR videos
    rr_recent_ids = [e["yt_video_id"] for e in rr_uploads if e.get("yt_video_id")][-5:]
    rr_views = fetch_yt_video_views(rr_recent_ids)

    # Compute YPP progress
    subs = channel.get("subs", 0)
    subs_pct = min(100, subs * 100 / YPP_SUBS_TARGET) if subs else 0
    subs_gap = max(0, YPP_SUBS_TARGET - subs)

    if want_json:
        print(json.dumps({
            "ts": now.isoformat(), "channel": channel,
            "pipelines_7d": per_pipeline,
            "rr_ad_safety_7d": rr_safety,
            "rr_recent_views": rr_views,
        }, indent=2))
        return

    # Pretty header
    print()
    hr()
    print(f"  PIPELINE DASHBOARD — {now.strftime('%Y-%m-%d %H:%M IST')}")
    hr()
    print()
    if "error" in channel:
        print(f"  Channel stats unavailable: {channel['error']}")
    else:
        print(f"  Channel: {channel['channel_title']}  (id={channel['channel_id']})")
        print(f"  Lifetime: {channel['subs']:>6} subs  ·  {channel['total_views']:>8} views  ·  {channel['video_count']:>4} videos")
        print()
        print("  YPP PROGRESS (Shorts path: 1K subs + 10M Shorts views / 90d)")
        print(f"    Subs:           {subs:>6} / {YPP_SUBS_TARGET}    ({subs_pct:5.1f}%)  gap: {subs_gap}")
        print(f"    Shorts views:   ? / {YPP_SHORTS_VIEWS_TARGET:,}  (90-day rolling needs broader OAuth to query)")
    print()

    hr()
    print("  LAST 7 DAYS — POSTS PER PIPELINE")
    hr()
    for name, info in PIPELINES.items():
        s = per_pipeline[name]
        marker = "ok" if s.get("posts", 0) > 0 and s.get("errors", 0) == 0 else \
                 "warn" if s.get("posts", 0) > 0 else "idle"
        print(f"  {marker:4s} {name:12s}  posts: {s.get('posts',0):>3}  "
              f"YT-ok: {s.get('success_yt',0):>3}  YT-fail: {s.get('fail_yt',0):>2}  "
              f"errors: {s.get('errors',0):>3}")
    print()

    hr()
    print("  RR AD-SAFETY (last 7 days, at-upload heuristic)")
    hr()
    total = sum(rr_safety.values()) or 1
    for tier, cnt in [("green", rr_safety.get("green", 0)),
                       ("yellow", rr_safety.get("yellow", 0)),
                       ("red", rr_safety.get("red", 0))]:
        bar = "█" * int(20 * cnt / total)
        print(f"  {tier:6s}  {cnt:>3} ({cnt*100/total:4.1f}%)  {bar}")
    print()

    if rr_views:
        hr()
        print("  RR RECENT VIDEO PERFORMANCE")
        hr()
        for vid, views in sorted(rr_views.items(), key=lambda x: -x[1]):
            print(f"  https://youtube.com/shorts/{vid}   views: {views:>5}")
        print()

    hr()
    print("  TOP RECOMMENDATION FOR THIS WEEK")
    hr()
    if "error" in channel:
        rec = "Auth issue — run `python3 ~/RedditReels/tools/check_monetization.py --auth` to unlock channel stats."
    elif channel["subs"] < 100:
        rec = ("You're cold-start. Pick your 3 best reels and post them to your personal "
               "IG/Twitter/WhatsApp to seed first 100 watchers. Algo needs proof humans want this.")
    elif channel["subs"] < YPP_SUBS_TARGET:
        rec = (f"{subs_gap} subs to YPP. Sub-conversion > view count now. "
               "Lean into storytime format (RR), de-emphasize quote cards (FW) if RR is outperforming.")
    else:
        rec = ("YPP eligible! Check YT Studio → Monetization tab and submit application. "
               "Once approved, revenue starts within ~7 days.")
    print(f"  → {rec}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    print_dashboard(args.json)


if __name__ == "__main__":
    main()
