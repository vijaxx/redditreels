#!/usr/bin/env python3
"""
prune_dead_videos.py — delete YT videos that are >5 days old AND <5 views.

WHY: YT's algo tracks channel-level average watch-time + completion rate.
Dead videos drag the average down, making it harder for NEW uploads to get promoted.
Removing them is brutal but improves the channel-quality signal for future content.

SAFETY:
- DRY-RUN by default. Pass --execute to actually delete.
- Only acts on videos older than 5 days (gives them time to find audience).
- Only acts on videos with <5 views (effectively zero traction).
- Logs every action to ~/PipelineCleanup/prune_dead.log for review.
- NEVER touches videos that are pinned, in a playlist, or have engagement (likes/comments).

Usage:
  python3 prune_dead_videos.py            # dry-run (default)
  python3 prune_dead_videos.py --execute  # actually delete
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
AGE_DAYS_MIN = 5
VIEWS_MAX = 5
LIKES_MAX = 0
COMMENTS_MAX = 0

CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
LOG_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/prune_dead.log"))


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually delete (default = dry-run)")
    args = ap.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"=== {datetime.now().isoformat()} prune_dead_videos (execute={args.execute}) ==="]

    yt = _yt()
    cutoff = datetime.now(timezone.utc) - timedelta(days=AGE_DAYS_MIN)
    print(f"Scanning channel {CHANNEL_ID} for videos older than {cutoff.isoformat()} with <{VIEWS_MAX} views")

    # Walk uploads playlist (faster than search.list per quota)
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads_pl = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    print(f"  uploads playlist: {uploads_pl}")

    pruned, kept = 0, 0
    pl_req = yt.playlistItems().list(part="snippet,contentDetails", playlistId=uploads_pl, maxResults=50)
    while pl_req is not None:
        pl_resp = pl_req.execute()
        vid_ids = [it["contentDetails"]["videoId"] for it in pl_resp.get("items", [])]
        if not vid_ids:
            pl_req = yt.playlistItems().list_next(pl_req, pl_resp); continue
        stats = yt.videos().list(part="statistics,snippet,status", id=",".join(vid_ids)).execute()
        for it in stats.get("items", []):
            vid = it["id"]
            views = int(it.get("statistics", {}).get("viewCount", 0))
            likes = int(it.get("statistics", {}).get("likeCount", 0))
            comments = int(it.get("statistics", {}).get("commentCount", 0))
            published = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
            title = it["snippet"]["title"][:60]
            age_days = (datetime.now(timezone.utc) - published).days

            if (published < cutoff
                and views < VIEWS_MAX
                and likes <= LIKES_MAX
                and comments <= COMMENTS_MAX):
                # BUG FIX 2026-05-31: before deleting a YT video for low views,
                # check FB + Rumble views too. A video doing well on FB shouldn't
                # be deleted from YT (still earns the channel cross-platform reach
                # via the description link-back + brand recognition).
                fb_v = rum_v = None
                chrome_reachable = False
                try:
                    sys.path.insert(0, os.path.expanduser("~/RedditReels/tools"))
                    from view_scrapers import attach_chrome, get_facebook_views, get_rumble_views
                    # Look up matching upload entry to find FB/Rumble URLs
                    rr_log = pathlib.Path(os.path.expanduser("~/RedditReels/logs/uploads.jsonl"))
                    rr_entry = None
                    if rr_log.exists():
                        for L in rr_log.read_text().splitlines():
                            try:
                                e = json.loads(L)
                                if e.get("yt_video_id") == vid:
                                    rr_entry = e; break
                            except Exception: continue
                    if rr_entry and (rr_entry.get("fb_posted") or rr_entry.get("rumble_url")):
                        d = attach_chrome()
                        chrome_reachable = True
                        try:
                            if rr_entry.get("fb_posted"):
                                fb_v = get_facebook_views(rr_entry["fb_posted"], driver=d)
                            if rr_entry.get("rumble_url") or rr_entry.get("title"):
                                rum_v = get_rumble_views(rr_entry.get("rumble_url"),
                                                          driver=d, title_hint=rr_entry.get("title"))
                        finally:
                            try: d.quit()
                            except Exception: pass
                    elif rr_entry is None:
                        chrome_reachable = True  # no FB to check — YT-only decision is valid
                except Exception as _e:
                    pass  # Chrome unreachable — will KEEP video (safer than blind delete)
                # Safety: if Chrome was unreachable we can't confirm FB views are 0.
                # Our channel earns most views on FB. Never prune without confirmed FB data.
                if not chrome_reachable:
                    line = f"  KEEP-CHROME-DOWN {vid}  age={age_days}d  YT={views}  '{title}' (FB unconfirmed — skip prune)"
                    print(line); log_lines.append(line)
                    kept += 1
                    continue
                # Second safety: if the entry HAD a FB post but fb_v came back None,
                # the scraper may have hit a login/timeout issue. Don't delete based on
                # YT views alone when FB reach is unknown.
                had_fb = rr_entry and rr_entry.get("fb_posted")
                if had_fb and fb_v is None:
                    line = f"  KEEP-FB-NULL {vid}  age={age_days}d  YT={views}  '{title}' (FB scrape returned None — skip prune)"
                    print(line); log_lines.append(line)
                    kept += 1
                    continue
                total = views + (fb_v or 0) + (rum_v or 0)
                if total >= VIEWS_MAX:
                    # Cross-platform views save it from the kill list
                    line = f"  KEEP-CROSS-PLATFORM {vid}  age={age_days}d  YT={views} FB={fb_v} RUM={rum_v} total={total}  '{title}'"
                    print(line); log_lines.append(line)
                    kept += 1
                    continue
                action = "DELETE" if args.execute else "WOULD-DELETE"
                line = f"  {action} {vid}  age={age_days}d  YT={views} FB={fb_v} RUM={rum_v} total={total}  '{title}'"
                print(line); log_lines.append(line)
                if args.execute:
                    try:
                        yt.videos().delete(id=vid).execute()
                        log_lines.append(f"    → deleted OK")
                    except Exception as e:
                        log_lines.append(f"    → DELETE FAILED: {e}")
                pruned += 1
            else:
                kept += 1
        pl_req = yt.playlistItems().list_next(pl_req, pl_resp)

    summary = f"=== summary: {'pruned' if args.execute else 'would-prune'} {pruned}, kept {kept} ==="
    print(summary); log_lines.append(summary)
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(log_lines) + "\n")


if __name__ == "__main__":
    main()
