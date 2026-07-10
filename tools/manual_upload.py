#!/usr/bin/env python3
"""One-off manual upload of a specific video file to YT + FB + Rumble.
Reuses redditreels' platform upload functions. Each platform is independent —
a failure on one does not stop the others."""
import sys
from pathlib import Path

BASE = Path("/Users/vijaxx/RedditReels")
sys.path.insert(0, str(BASE))
import redditreels as rr

VIDEO = Path("/Users/vijaxx/Desktop/The_Pattern_Short.mp4")
TITLE = "You're Not In Control — And I Can Prove It"
CAPTION = (
    "Right now, you think you're in control. You're not. \U0001F9E0\n\n"
    "You decided three seconds ago — your brain just hasn't told you yet. "
    "Your choices follow patterns you've never noticed... but the moment you SEE "
    "the pattern, you finally get to break it. Most people never try.\n\n"
    "So the only question is — will you?\n\n"
    "\U0001F4AC Comment \"PATTERN\" if you're ready to break yours.\n"
    "\U0001F3AC Follow FrameWise Cinema for daily mindset shorts.\n\n"
    "#mindset #psychology #selfawareness #breakthepattern #motivation #discipline "
    "#mentalstrength #stoicism #personalgrowth #shorts #reels #fyp"
)
TAGS = ["mindset", "psychology", "self awareness", "break the pattern", "motivation",
        "discipline", "mental strength", "stoicism", "personal growth", "human behavior",
        "subconscious", "self improvement", "shorts"]

cfg = rr.load_cfg()
results = {}

print(f"Uploading: {VIDEO.name}\nTitle: {TITLE}\n")

# --- YouTube (public) ---
try:
    vid = rr.upload_youtube(VIDEO, TITLE, CAPTION, TAGS, cfg, "public")
    results["youtube"] = f"https://youtube.com/shorts/{vid}"
    print(f"YouTube ✅ {results['youtube']}", flush=True)
except Exception as e:
    print(f"YouTube ❌ {e}", flush=True)

# --- Facebook ---
try:
    results["facebook"] = rr.upload_facebook(VIDEO, TITLE, CAPTION, TAGS, cfg)
    print(f"Facebook ✅ {results['facebook']}", flush=True)
except Exception as e:
    print(f"Facebook ❌ {e}", flush=True)

# --- Rumble ---
try:
    results["rumble"] = rr.upload_rumble(VIDEO, TITLE, CAPTION, TAGS, cfg)
    print(f"Rumble ✅ {results['rumble']}", flush=True)
except Exception as e:
    print(f"Rumble ❌ {e}", flush=True)

print("\n=== DONE ===")
for k, v in results.items():
    print(f"  {k}: {v}")
