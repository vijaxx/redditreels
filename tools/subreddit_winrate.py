#!/usr/bin/env python3
"""
subreddit_winrate.py — track per-subreddit performance.

For each subreddit we've pulled stories from, compute avg performance score
over last 14 days. Subs averaging < threshold get auto-removed from the SUBS
pool (writes to ~/PipelineCleanup/subreddit_blacklist.json which fetch_story
checks).

Runs weekly.

Built 2026-06-03 overnight round 2.
"""
import json, pathlib
from collections import defaultdict
from datetime import datetime, timedelta

UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
SCORES = pathlib.Path.home() / "PipelineCleanup" / "performance_scores.jsonl"
BLACKLIST = pathlib.Path.home() / "PipelineCleanup" / "subreddit_blacklist.json"
REPORT = pathlib.Path.home() / "PipelineCleanup" / "subreddit_winrate.md"

MIN_VIDEOS = 3   # need at least N videos in last 14d to judge
MIN_AVG_SCORE = 12  # avg score < this = blacklist for 7 days
LOOKBACK_DAYS = 14


def run():
    if not UPLOADS.exists():
        print("no uploads"); return
    # video_id → (sub, ts)
    meta = {}
    cutoff = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    for line in UPLOADS.read_text().splitlines():
        try:
            e = json.loads(line)
            vid = e.get("yt_video_id"); sub = e.get("sub"); ts = e.get("ts","")
            if vid and sub and ts >= f"{cutoff}_000000":
                meta[vid] = sub
        except: continue
    if not meta:
        print("no recent uploads"); return
    # video_id → latest score
    score = {}
    if SCORES.exists():
        for line in SCORES.read_text().splitlines():
            try:
                s = json.loads(line)
                score[s["video_id"]] = s["score"]  # last wins
            except: continue
    by_sub = defaultdict(list)
    for vid, sub in meta.items():
        if vid in score:
            by_sub[sub].append(score[vid])
    rows = []
    blacklist = []
    for sub, scores in sorted(by_sub.items()):
        n = len(scores)
        avg = sum(scores)/n
        rows.append((sub, n, avg))
        if n >= MIN_VIDEOS and avg < MIN_AVG_SCORE:
            blacklist.append(sub)
    # Write report
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Subreddit win-rate — last {LOOKBACK_DAYS} days", "",
             "| Sub | n videos | avg score | status |", "|---|---|---|---|"]
    for sub, n, avg in sorted(rows, key=lambda r: -r[2]):
        st = "🔴 blacklist" if sub in blacklist else "✓ keep"
        lines.append(f"| r/{sub} | {n} | {avg:.1f} | {st} |")
    REPORT.write_text("\n".join(lines))
    # Write blacklist
    BLACKLIST.write_text(json.dumps({
        "generated": datetime.now().isoformat(),
        "expires": (datetime.now() + timedelta(days=7)).isoformat(),
        "blacklisted_subs": blacklist,
    }, indent=2))
    print(f"✓ report: {REPORT}")
    print(f"  blacklisted: {blacklist or 'none'}")


if __name__ == "__main__": run()
