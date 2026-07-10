#!/usr/bin/env python3
"""Runs every 30 minutes (or on demand) and fixes the failure modes that
come up in practice: Chrome on :9223 died (relaunch it), an uploader process
hung for over an hour (kill it), disk space under 5GB (clean up aggressively),
a failure streak (notify), or the launchd job somehow got unloaded (reload it)."""
import os, sys, json, pathlib, subprocess, urllib.request
from datetime import datetime


HOME = pathlib.Path.home()
LOG = HOME / "PipelineCleanup" / "self_heal.log"


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _within_chrome_window() -> bool:
    """Chrome is only needed for posting slots. As of 2026-06-15 the fires moved to
    India-evening IST fires: 18:00 / 19:30 / 21:00 / 22:30. Pre-warm 17:00–23:00 IST.
    redditreels.py also calls ensure_chrome.sh itself at post-time; self_heal only
    relaunches if Chrome is actually dead, so a healthy Chrome causes no repeat spawns
    (no GPU flicker)."""
    return 17 <= datetime.now().hour < 23


def heal_chrome() -> bool:
    """If :9223 dead → relaunch via ensure_chrome.sh.
    Gated to posting window only (see _within_chrome_window)."""
    if not _within_chrome_window():
        return False  # outside posting hours — leave Chrome dead, save GPU
    try:
        urllib.request.urlopen("http://127.0.0.1:9223/json/version", timeout=3).read()
        return False  # alive, no heal needed
    except Exception:
        _log(" Chrome :9223 DEAD — relaunching via ensure_chrome.sh")
        try:
            r = subprocess.run(["/bin/zsh", str(HOME / "RedditReels/ensure_chrome.sh")],
                                capture_output=True, text=True, timeout=60)
            _log(f"  ensure_chrome → exit {r.returncode}")
            return True
        except Exception as e:
            _log(f"  relaunch failed: {e}")
            return False


def heal_ollama() -> bool:
    """If Ollama server (local free LLM — the brain for the whole pipeline) is
    down, relaunch the app. Added 2026-06-04 after switching off paid Claude."""
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3).read()
        return False  # up, no heal needed
    except Exception:
        _log(" Ollama DOWN — relaunching Ollama.app")
        try:
            subprocess.run(["open", "-a", "Ollama"], capture_output=True, timeout=30)
            # give it a moment to boot
            import time as _t
            for _ in range(15):
                _t.sleep(2)
                try:
                    urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3).read()
                    _log("   Ollama back up")
                    return True
                except Exception:
                    continue
            _log("   Ollama still not responding after relaunch")
            return True
        except Exception as e:
            _log(f"  relaunch failed: {e}")
            return False


def heal_stale_python() -> int:
    """Kill any redditreels.py process running > 60 min (probably hung)."""
    killed = 0
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,etime,command"], text=True)
        for line in out.split("\n"):
            if "redditreels.py" not in line: continue
            parts = line.strip().split(None, 2)
            if len(parts) < 3: continue
            pid, etime, cmd = parts
            # etime format: "MMM:SS" or "HH:MM:SS" or "DD-HH:MM:SS"
            mins = 0
            if "-" in etime:
                d, hms = etime.split("-")
                mins += int(d) * 1440
                etime = hms
            colons = etime.count(":")
            try:
                if colons == 1: mm, ss = etime.split(":"); mins += int(mm)
                elif colons == 2: h, mm, ss = etime.split(":"); mins += int(h)*60 + int(mm)
            except: continue
            if mins > 60:
                _log(f" STALE pid {pid} ({mins}min) — killing")
                subprocess.run(["kill", "-TERM", pid])
                killed += 1
    except Exception as e:
        _log(f"  ps scan failed: {e}")
    return killed


def heal_disk() -> bool:
    """If <5GB free → aggressive cleanup."""
    try:
        df = subprocess.check_output(["df", "-k", str(HOME)], text=True)
        free_kb = int(df.strip().split("\n")[-1].split()[3])
        free_gb = free_kb / 1024 / 1024
        if free_gb < 5:
            _log(f" DISK low ({free_gb:.1f} GB free) — aggressive cleanup")
            # Delete old reels + processing >3d
            subprocess.run(["find", str(HOME / "RedditReels/reels"), "-name", "*.mp4",
                             "-mtime", "+3", "-delete"], capture_output=True)
            subprocess.run(["find", str(HOME / "RedditReels/processing"), "-maxdepth", "1",
                             "-type", "d", "-mtime", "+3", "-exec", "rm", "-rf", "{}", "+"],
                            capture_output=True)
            return True
    except Exception as e:
        _log(f"  disk check failed: {e}")
    return False


def heal_launchd() -> bool:
    """Re-load critical launchd jobs if missing."""
    fixed = False
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True)
        for label, plist in [
            ("com.redditreels.pipeline", HOME / "Library/LaunchAgents/com.redditreels.pipeline.plist"),
            ("com.pipelines.morningbatch", HOME / "Library/LaunchAgents/com.pipelines.morningbatch.plist"),
        ]:
            if label not in out:
                _log(f" launchd missing: {label} — reloading")
                if plist.exists():
                    subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
                    fixed = True
    except Exception as e:
        _log(f"  launchd check failed: {e}")
    return fixed


def run():
    _log("=== self_heal cycle ===")
    actions = []
    # 2026-06-13: Ollama REMOVED (Groq is the brain now) — no longer relaunch it.
    # if heal_ollama(): actions.append("ollama")
    if heal_chrome(): actions.append("chrome")
    n = heal_stale_python()
    if n: actions.append(f"killed-{n}-stale")
    if heal_disk(): actions.append("disk")
    if heal_launchd(): actions.append("launchd")
    _log(f"=== healed: {actions or 'nothing — all healthy'} ===")
    # Notify on healing actions
    if actions:
        try:
            sys.path.insert(0, str(HOME / "RedditReels/tools"))
            from notify import notify
            notify(" Self-heal triggered", f"Actions: {', '.join(actions)}")
        except: pass


if __name__ == "__main__": run()
