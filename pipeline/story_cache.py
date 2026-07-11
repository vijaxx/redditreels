#!/usr/bin/env python3
"""
story_cache.py — decouple fires from Reddit's RSS rate limit (the recurring 429 that kept
killing fires).

  fill  : ONE gentle, spaced sweep → a vetted story pool in logs/story_cache.json.
          Run when Reddit is healthy (daily via morning_batch). Touches Reddit only here.
  pop   : return the next unused story dict (NO Reddit hit). fetch_story.py calls this FIRST,
          so the 4 daily fires consume from the cache and never hit Reddit's rate limit.

Each cached item is a normal story dict {subreddit,title,selftext,url,author,source_type}.
"""
from __future__ import annotations
import json, pathlib, sys, time, random
from typing import Any

# reuse fetch_story's fetchers/validators (import is safe — main() is guarded)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_story as fs

ROOT  = pathlib.Path.home() / "RedditReels"
CACHE = ROOT / "logs" / "story_cache.json"
USED  = ROOT / "logs" / "used_stories.json"

Story = dict[str, Any]


def _used_urls() -> set[str]:
    try:
        return {u["url"] for u in json.loads(USED.read_text())}
    except Exception:
        return set()


def load() -> list[Story]:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return []


def save(items: list[Story]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(items, indent=2, ensure_ascii=False))


def fill(target: int = 25) -> int:
    """Gentle sweep across stable subs (spaced 3s) until the pool reaches `target`."""
    used = _used_urls()
    pool = load()
    have = {s["url"] for s in pool}
    # The 4 daily fires consume from this cache, so the cache is what actually decides the
    # channel's identity. Sweep the "Am I The Villain?" series cluster first (shuffled), then
    # the off-brand subs as fallback -- fill() stops once it hits `target`, so the pool ends up
    # series-dominated, with off-brand subs only kicking in if the series ones are blacklisted
    # or run dry. This is what makes the series narrowing actually live, not just cosmetic.
    series = [s for s in fs.SERIES_SUBS if s in fs.SUBS]
    other  = [s for s in fs.SUBS if s not in fs.SERIES_SUBS]
    random.shuffle(series); random.shuffle(other)
    subs = series + other
    added = 0
    for i, sub in enumerate(subs):
        if len(pool) >= target:
            break
        if i:
            time.sleep(3)                          # spaced → never a burst → no 429
        rss = fs.fetch_rss(sub, t=None)
        if not rss:
            continue
        for e in fs.parse_entries(rss, sub):
            if len(pool) >= target:
                break
            if fs.is_good(e) and e["url"] not in used and e["url"] not in have:
                e["source_type"] = "post"
                pool.append(e)
                have.add(e["url"])
                added += 1
        print(f"  r/{sub}: pool now {len(pool)}")
    save(pool)
    print(f"[story_cache] fill: +{added} this run → {len(pool)} stories cached")
    return len(pool)


def pop() -> Story | None:
    """Return + remove the next cached story (FIFO). None if cache empty."""
    pool = load()
    if not pool:
        return None
    story = pool.pop(0)
    save(pool)
    return story


if __name__ == "__main__":
    if "--fill" in sys.argv:
        fill()
    elif "--pop" in sys.argv:
        s = pop()
        print(json.dumps(s, ensure_ascii=False) if s else "(cache empty)")
    else:
        print(f"[story_cache] {len(load())} stories cached")
