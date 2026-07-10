#!/usr/bin/env python3
"""Rests a hashtag once it's shown up in more than half of the last 20
videos -- reusing the same tags on the same channel stops bringing in new
audience, so this keeps a rotation pool and swaps in fresher alternatives.
hashtag_miner.py calls this to filter its output."""
import json, pathlib
from collections import Counter
from typing import List

UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
LOOKBACK = 20  # last N videos
BURNOUT_FRACTION = 0.5  # if used in >50% of recent → rest
REST_FOR_VIDEOS = 8


def get_overused_tags() -> set:
    """Return set of hashtags used in > BURNOUT_FRACTION of recent uploads."""
    if not UPLOADS.exists(): return set()
    recent_tags = Counter()
    n_videos = 0
    for line in UPLOADS.read_text().splitlines()[-LOOKBACK:]:
        try:
            e = json.loads(line)
            tags = e.get("tags") or e.get("plain_tags") or []
            for t in tags:
                recent_tags[t.lower().lstrip("#")] += 1
            n_videos += 1
        except: continue
    if n_videos < 5:
        return set()  # not enough data yet
    threshold = n_videos * BURNOUT_FRACTION
    return {t for t, c in recent_tags.items() if c >= threshold}


def filter_tags(tags: List[str]) -> List[str]:
    """Remove burned-out tags from a list. Preserves order."""
    burned = get_overused_tags()
    return [t for t in tags if t.lower().lstrip("#") not in burned]


if __name__ == "__main__":
    burned = get_overused_tags()
    print(f"Burned-out tags (used >{int(BURNOUT_FRACTION*100)}% of last {LOOKBACK} videos):")
    for t in sorted(burned):
        print(f"  #{t}")
    if not burned:
        print("  (none — not enough data, or no over-use detected)")
