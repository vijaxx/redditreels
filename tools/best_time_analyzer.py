#!/usr/bin/env python3
"""
best_time_analyzer.py — find which fire times actually generate the most views.

We fire at 17:30/18:30/19:30/20:30 IST. Are all 4 equally good? Probably not.
This walks performance_scores + uploads to compute avg views/score by fire hour
across all-time data. Outputs recommendation: "shift 18:30 → 19:00" etc.

Run weekly.

Built 2026-06-03 overnight.
"""
import json, pathlib
from collections import defaultdict
from datetime import datetime

SCORES = pathlib.Path.home() / "PipelineCleanup" / "performance_scores.jsonl"
UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
OUT = pathlib.Path.home() / "PipelineCleanup" / "best_time_analysis.md"


def run():
    if not UPLOADS.exists():
        print("no uploads.jsonl"); return
    # video_id → upload hour (IST)
    hour_by_vid = {}
    for line in UPLOADS.read_text().splitlines():
        try:
            e = json.loads(line)
            vid = e.get("yt_video_id")
            ts = e.get("ts")  # "20260603_173000"
            if vid and ts and "_" in ts:
                hh = int(ts.split("_")[1][:2])
                hour_by_vid[vid] = hh
        except: continue
    # Latest score per video
    latest_score = {}
    latest_views = {}
    if SCORES.exists():
        for line in SCORES.read_text().splitlines():
            try:
                s = json.loads(line)
                vid = s["video_id"]
                latest_score[vid] = s["score"]
                latest_views[vid] = s["raw"]["total_views"]
            except: continue
    if not (hour_by_vid and latest_score):
        print("not enough joined data yet"); return
    # Group by hour
    by_hour = defaultdict(lambda: {"scores": [], "views": []})
    for vid, hh in hour_by_vid.items():
        if vid in latest_score:
            by_hour[hh]["scores"].append(latest_score[vid])
            by_hour[hh]["views"].append(latest_views.get(vid, 0))
    # Build markdown
    lines = [f"# Best-time-to-post analysis ({datetime.now():%Y-%m-%d})", "",
             "| Hour IST | n videos | avg score | avg views |", "|---|---|---|---|"]
    rows = []
    for hh in sorted(by_hour.keys()):
        n = len(by_hour[hh]["scores"])
        avg_score = sum(by_hour[hh]["scores"])/n if n else 0
        avg_views = sum(by_hour[hh]["views"])/n if n else 0
        rows.append((hh, n, avg_score, avg_views))
        lines.append(f"| {hh:02d}:30 | {n} | {avg_score:.1f} | {avg_views:.1f} |")
    if rows:
        best = max(rows, key=lambda r: r[2])
        worst = min(rows, key=lambda r: r[2])
        lines.append("")
        lines.append(f"**Best:** {best[0]:02d}:30 (avg score {best[2]:.1f})")
        lines.append(f"**Worst:** {worst[0]:02d}:30 (avg score {worst[2]:.1f})")
        if best[2] > worst[2] * 1.5:
            lines.append("")
            lines.append(f"💡 RECOMMENDATION: shift the {worst[0]:02d}:30 slot to closer to {best[0]:02d}:30")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"  ✓ {OUT}")
    for hh, n, s, v in rows:
        print(f"  {hh:02d}:30  n={n}  score={s:.1f}  views={v:.1f}")


if __name__ == "__main__": run()
