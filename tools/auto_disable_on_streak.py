#!/usr/bin/env python3
"""
auto_disable_on_streak.py — emergency brake.

If 3 consecutive RR fires fail (no successful upload), unload the launchd job +
push Telegram alert. Prevents continued failure from creating dead videos /
burning quota / damaging channel scores.

To re-enable: `launchctl load ~/Library/LaunchAgents/com.redditreels.pipeline.plist`

Built 2026-06-03 overnight round 2.
"""
import json, pathlib, subprocess
from datetime import datetime

UPLOADS = pathlib.Path.home() / "RedditReels" / "logs" / "uploads.jsonl"
PIPELINE_LOG = pathlib.Path.home() / "RedditReels" / "logs" / "pipeline.log"
LOG = pathlib.Path.home() / "PipelineCleanup" / "auto_disable.log"
ALERT = pathlib.Path.home() / "PipelineCleanup" / "PIPELINE_DISABLED.md"

CONSECUTIVE_FAILURES_THRESHOLD = 3


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def recent_fires_status() -> list:
    """Return list of (timestamp, status) for last N fires.
    status: 'success' if uploads.jsonl entry exists with at least 1 platform,
            'failure' if a fire START was logged but no successful entry."""
    if not UPLOADS.exists():
        return []
    # Get last 5 entries
    recent = []
    for line in UPLOADS.read_text().splitlines()[-5:]:
        try:
            e = json.loads(line)
            ok = any(e.get(k) for k in ["yt_video_id", "fb_posted", "rumble_url"])
            recent.append((e.get("ts","?"), "success" if ok else "failure"))
        except: continue
    return recent


def run():
    fires = recent_fires_status()
    if not fires:
        _log("no fires yet"); return
    last_n = fires[-CONSECUTIVE_FAILURES_THRESHOLD:]
    all_failed = len(last_n) >= CONSECUTIVE_FAILURES_THRESHOLD and all(s == "failure" for _, s in last_n)
    if not all_failed:
        _log(f"OK — last {len(last_n)} fires: {[s for _,s in last_n]}")
        # Clear alert if it exists (recovered)
        if ALERT.exists(): ALERT.unlink()
        return
    # FAIL: unload + alert
    _log(f"⚠️ {CONSECUTIVE_FAILURES_THRESHOLD} consecutive failures — unloading RR launchd")
    try:
        subprocess.run(["launchctl", "unload",
                         str(pathlib.Path.home() / "Library/LaunchAgents/com.redditreels.pipeline.plist")],
                        capture_output=True)
        _log("  ✓ launchd unloaded")
    except Exception as e:
        _log(f"  ✗ unload failed: {e}")
    body = (f"RR fires failed {CONSECUTIVE_FAILURES_THRESHOLD} times in a row.\n"
            f"Pipeline launchd has been UNLOADED to prevent further damage.\n\n"
            f"Recent fires:\n" + "\n".join(f"  {ts}: {s}" for ts, s in fires))
    ALERT.parent.mkdir(parents=True, exist_ok=True)
    ALERT.write_text(f"# 🛑 PIPELINE DISABLED ({datetime.now()})\n\n{body}\n\n"
                     f"To re-enable: `launchctl load ~/Library/LaunchAgents/com.redditreels.pipeline.plist`\n")
    try:
        import sys
        sys.path.insert(0, str(pathlib.Path.home() / "RedditReels/tools"))
        from notify import notify
        notify("🛑 RR PIPELINE DISABLED", body)
    except Exception as e:
        _log(f"  notify failed: {e}")


if __name__ == "__main__": run()
