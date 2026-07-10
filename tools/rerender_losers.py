#!/usr/bin/env python3
"""
rerender_losers.py — find reels with <50 views after 6h, re-render with different hook angle.

WHY: First 2-6 hours determines the algo's classification. <50 views at 6h means YT
gave up on it. Re-uploading the SAME story with a NEW hook gives it a second roll.

Logic:
  1. Walk recent uploads.jsonl entries (last 24h)
  2. For each YT video uploaded 6-12 hours ago with <50 views, mark as loser
  3. For each loser, regenerate the script (Claude with explicit "try a different angle")
  4. Render the new variant
  5. Upload as a new video (the original stays — or pair with auto-prune later)

Default: dry-run. Pass --execute to actually re-render + re-upload.
Cron: fires hourly between 09:00-22:00 IST.
"""
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, sys
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
UPLOADS_LOG = pathlib.Path(os.path.expanduser("~/RedditReels/logs/uploads.jsonl"))
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
ALREADY_RERENDERED = pathlib.Path(os.path.expanduser("~/PipelineCleanup/already_rerendered.json"))
LOG_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/rerender_losers.log"))

VIEW_THRESHOLD = 50
AGE_HOURS_MIN = 6
AGE_HOURS_MAX = 12  # don't re-roll videos too old

# Per-day cap — even if multiple losers exist, re-render at most this many per day.
# Prevents the morning batch from accidentally producing 3+ extra uploads.
MAX_RERENDERS_PER_DAY = 1
DAILY_COUNT_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/rerender_daily_count.json"))


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


def load_set(p):
    if not p.exists(): return set()
    try: return set(json.loads(p.read_text()))
    except Exception: return set()


def save_set(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(s)))


def find_losers(yt, already):
    """Identify videos uploaded 6-12h ago with <50 TOTAL views (YT+FB+Rumble),
    not already re-rendered. Bug fix 2026-05-31: previously judged YT alone,
    which incorrectly flagged a denture-solution video with 1 YT view + 13 FB
    views + 19 Rumble views (33 total) as a loser. Now aggregates across all 3."""
    if not UPLOADS_LOG.exists(): return []
    entries = []
    for line in UPLOADS_LOG.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        if e.get("yt_video_id") and e["yt_video_id"] not in already:
            entries.append(e)
    if not entries: return []

    now = datetime.now(timezone.utc)
    cutoff_old = now - timedelta(hours=AGE_HOURS_MAX)
    cutoff_new = now - timedelta(hours=AGE_HOURS_MIN)

    ids_to_check = []
    for e in entries:
        try:
            ts = datetime.strptime(e["ts"], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except Exception: continue
        if cutoff_old <= ts <= cutoff_new:
            ids_to_check.append((e, ts))
    if not ids_to_check: return []

    print(f"  {len(ids_to_check)} candidates in age window — pulling cross-platform views")

    # Import scraper (lazy so dry-run without Chrome still works for YT-only sanity check)
    sys.path.insert(0, os.path.expanduser("~/RedditReels/tools"))
    try:
        from view_scrapers import get_views_all_platforms
        scrape_ok = True
    except Exception as e:
        print(f"   view_scrapers unavailable ({e}) — falling back to YT-only")
        scrape_ok = False

    losers = []
    for e, ts in ids_to_check:
        age_h = (now - ts).total_seconds() / 3600
        # SKIP non-public videos (2026-06-05): a private/unlisted video was either
        # intentionally PULLED (e.g. ad-unsafe content set to private) or held back —
        # it is NOT an organic "loser". Re-rendering it re-PUBLISHES pulled content,
        # which is exactly how the trans-misogyny pull got re-uploaded today. Only
        # genuine public videos with low views qualify as losers.
        try:
            vst = yt.videos().list(part="status", id=e["yt_video_id"]).execute()
            if vst.get("items"):
                pstat = vst["items"][0]["status"].get("privacyStatus")
                if pstat != "public":
                    print(f"    {e['yt_video_id']}  privacy={pstat} → SKIP (pulled/held, not a loser)")
                    continue
        except Exception as _pe:
            print(f"     privacy check failed for {e['yt_video_id']}: {_pe} — skipping to be safe")
            continue
        if scrape_ok:
            try:
                r = get_views_all_platforms(
                    yt_id=e.get("yt_video_id"),
                    fb_url=e.get("fb_posted") or e.get("facebook_url"),
                    rumble_url=e.get("rumble_url"),
                    rumble_title_hint=e.get("title"),
                )
                total = r["total"]
                print(f"    {e['yt_video_id']}  YT={r['youtube']} FB={r['facebook']} RUM={r['rumble']}  total={total}")
            except Exception as ex:
                print(f"     scrape failed for {e['yt_video_id']}: {ex} — falling back to YT-only")
                stats = yt.videos().list(part="statistics", id=e["yt_video_id"]).execute()
                total = int(stats["items"][0]["statistics"].get("viewCount", 0)) if stats.get("items") else 0
                r = {"youtube": total, "facebook": None, "rumble": None, "total": total}
        else:
            # Chrome/scraper unavailable — if video was posted to FB, skip rather than
            # judge it on YT-only views (FB is the primary view driver; YT=2/FB=228 is
            # common and would be wrongly rerendered if FB data is missing).
            if e.get("fb_posted") or e.get("facebook_url"):
                print(f"    {e['yt_video_id']}   scraper down, has FB URL — SKIP (can't judge without FB)")
                continue
            stats = yt.videos().list(part="statistics", id=e["yt_video_id"]).execute()
            total = int(stats["items"][0]["statistics"].get("viewCount", 0)) if stats.get("items") else 0
            r = {"youtube": total, "facebook": None, "rumble": None, "total": total}
        if total < VIEW_THRESHOLD:
            losers.append({**e, "current_views": total, "view_breakdown": r, "age_hours": age_h})
    return losers


def rerender_with_alt_hook(entry: dict) -> bool:
    """Re-run RR orchestrator forcing the SAME story but with explicit hook-variation."""
    story_url = entry.get("story_url")
    if not story_url:
        print(f"  no story_url for {entry.get('yt_video_id')} — can't re-render"); return False
    # Set env so rewrite_story picks up the "different angle" directive
    env = os.environ.copy()
    env["RR_FORCE_STORY_URL"] = story_url
    env["RR_ALT_HOOK"] = "1"
    cmd = ["/usr/bin/python3", os.path.expanduser("~/RedditReels/redditreels.py"), "--public"]
    print(f"  → re-rendering with alt hook")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        print(f"    re-render FAILED: {proc.stderr[-500:]}"); return False
    print(f"    re-render OK")
    return True


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _get_today_count() -> int:
    if not DAILY_COUNT_PATH.exists(): return 0
    try:
        d = json.loads(DAILY_COUNT_PATH.read_text())
        return d.get(_today_str(), 0)
    except Exception: return 0


def _bump_today_count():
    today = _today_str()
    d = {}
    if DAILY_COUNT_PATH.exists():
        try: d = json.loads(DAILY_COUNT_PATH.read_text())
        except Exception: pass
    d[today] = d.get(today, 0) + 1
    # Keep only last 14 days
    d = {k: v for k, v in d.items() if k >= (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")}
    DAILY_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_COUNT_PATH.write_text(json.dumps(d, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually re-render + re-upload")
    args = ap.parse_args()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = [f"=== {datetime.now().isoformat()} rerender_losers (execute={args.execute}) ==="]

    # Hard daily cap — never produce more than N rerenders/day (user wants ≤4 uploads/day total
    # = 3 scheduled + max 1 rerender recovery)
    today_count = _get_today_count()
    if today_count >= MAX_RERENDERS_PER_DAY:
        line = f"  SKIP — already {today_count} rerenders today (cap = {MAX_RERENDERS_PER_DAY})"
        print(line); log.append(line)
        with open(LOG_PATH, "a") as f:
            f.write("\n".join(log) + "\n")
        return

    yt = _yt()
    already = load_set(ALREADY_RERENDERED)
    losers = find_losers(yt, already)
    print(f"Found {len(losers)} losers (views<{VIEW_THRESHOLD} after {AGE_HOURS_MIN}-{AGE_HOURS_MAX}h)")
    print(f"Today's rerender count: {today_count}/{MAX_RERENDERS_PER_DAY}")

    rendered_this_run = 0
    for L in losers:
        if rendered_this_run >= (MAX_RERENDERS_PER_DAY - today_count):
            print(f"  SKIP rest — hit daily cap"); log.append("  hit daily cap, skipping rest")
            break
        br = L.get("view_breakdown", {})
        line = (f"  LOSER  vid={L['yt_video_id']}  age={L['age_hours']:.1f}h  "
                f"total={L['current_views']} (YT={br.get('youtube')} FB={br.get('facebook')} RUM={br.get('rumble')})  "
                f"title='{L.get('title','?')[:55]}'")
        print(line); log.append(line)
        if args.execute:
            if rerender_with_alt_hook(L):
                already.add(L["yt_video_id"])
                rendered_this_run += 1
                _bump_today_count()
                log.append(f"    → re-rendered (today count now {today_count + rendered_this_run})")
    save_set(ALREADY_RERENDERED, already)
    summary = f"=== summary: {len(losers)} losers, {rendered_this_run} rerendered ({'executed' if args.execute else 'dry-run'}) ==="
    print(summary); log.append(summary)
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
