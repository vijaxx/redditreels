#!/usr/bin/env python3
"""One-off: post an already-rendered reel to Facebook only (catch-up).

The reel is already on YouTube + Rumble; this puts it on FB without re-posting
elsewhere. Reuses redditreels' own build_description + upload_facebook so the
caption matches what went to YT/Rumble. Usage: fb_catchup.py <TS> [<TS> ...]
"""
import sys, json, time
from pathlib import Path

BASE = Path.home() / "RedditReels"
sys.path.insert(0, str(BASE))
import redditreels as rr

cfg = rr.load_cfg()

for ts in sys.argv[1:]:
    proc = BASE / "processing" / ts
    try:
        script = json.loads((proc / "script.json").read_text())
        story = json.loads((proc / "story.json").read_text())
        bg = proc / "bg_credit.txt"
        bg_credit = bg.read_text().strip() if bg.exists() else None
        reel = next(BASE.glob(f"reels/{ts}_*.mp4"))
    except Exception as e:
        print(f"[{ts}] SKIP — missing assets: {e}")
        continue

    description, tags, fb_description = rr.build_description(script, story, cfg, bg_credit=bg_credit)
    title = script["title"][:95]
    print(f"\n[{ts}] Posting to FB: {title!r}  ({reel.name})", flush=True)
    try:
        url = rr.upload_facebook(reel, title, fb_description, tags, cfg)
        print(f"[{ts}] FB  {url}", flush=True)
    except Exception as e:
        print(f"[{ts}] FB  {e}", flush=True)
    time.sleep(5)
