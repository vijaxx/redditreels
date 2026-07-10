#!/usr/bin/env python3
"""
hashtag_effectiveness.py — track which hashtags correlate with view spikes.

Walks performance_scores.jsonl + uploads.jsonl. Joins on yt_video_id. For each
tag, computes avg score for videos containing that tag. Outputs ranking.

Run weekly to see which tags actually drive performance vs. which are dead weight.
"""
import json, pathlib
from collections import defaultdict

SCORES = pathlib.Path.home() / "PipelineCleanup" / "performance_scores.jsonl"
UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
OUT = pathlib.Path.home() / "PipelineCleanup" / "hashtag_effectiveness.md"


def run():
    if not SCORES.exists() or not UPLOADS.exists():
        print("missing inputs"); return
    # video_id → tags
    tags_by_vid = {}
    for line in UPLOADS.read_text().splitlines():
        try:
            e = json.loads(line)
            vid = e.get("yt_video_id")
            tags = e.get("tags", []) or e.get("plain_tags", [])
            if vid and tags: tags_by_vid[vid] = [t.lower().lstrip("#") for t in tags]
        except: continue
    # Latest score per video
    latest_score = {}
    for line in SCORES.read_text().splitlines():
        try:
            s = json.loads(line)
            vid = s.get("video_id")
            if vid: latest_score[vid] = s["score"]
        except: continue
    # tag → list of scores
    tag_scores = defaultdict(list)
    for vid, score in latest_score.items():
        for t in tags_by_vid.get(vid, []):
            tag_scores[t].append(score)
    if not tag_scores:
        print("no joined data yet"); return
    # Sort by avg score (need at least 2 uses to be meaningful)
    ranking = [(t, sum(s)/len(s), len(s)) for t, s in tag_scores.items() if len(s) >= 2]
    ranking.sort(key=lambda x: x[1], reverse=True)
    lines = [f"# Hashtag effectiveness — {pathlib.Path(__file__).name}",
             f"Generated: by performance scores join.", "",
             "| Tag | Avg Score | Uses |", "|---|---|---|"]
    for t, avg, n in ranking[:30]:
        lines.append(f"| #{t} | {avg:.1f} | {n} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"   {OUT}")
    for t, avg, n in ranking[:5]:
        print(f"  TOP: #{t} avg={avg:.1f} (n={n})")


if __name__ == "__main__": run()
