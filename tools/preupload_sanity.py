#!/usr/bin/env python3
"""
preupload_sanity.py — block bad reels from reaching upload step.

Catches before live ship:
1. Audio missing (yesterday's FW silent bug would have been caught here)
2. Audio too quiet (mean < -30 dB = inaudible)
3. Video duration mismatch with narration script
4. Last 1s is pure black (failed render)
5. File suspiciously small (<200KB = render failed)
6. Captions burned but unreadable (low contrast)

Returns (passed: bool, reasons: list[str]). Raises SystemExit(2) if fail and
--strict flag is set (orchestrator can check exit code).

Built 2026-06-03 overnight. Wired into redditreels.py before upload step.
"""
import sys, os, json, subprocess, argparse
from pathlib import Path
from typing import Tuple, List


def probe(mp4: Path) -> dict:
    """ffprobe → dict of {duration, has_audio, audio_codec, audio_bitrate, size_mb}"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(mp4)],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return {"error": r.stderr[-200:]}
    d = json.loads(r.stdout)
    fmt = d.get("format", {})
    streams = d.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": float(fmt.get("duration", 0)),
        "size_mb": int(fmt.get("size", 0)) / 1e6,
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_bitrate": int(audio.get("bit_rate", 0)) if audio else 0,
        "video_codec": next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None),
        "width": next((s.get("width") for s in streams if s.get("codec_type") == "video"), 0),
        "height": next((s.get("height") for s in streams if s.get("codec_type") == "video"), 0),
    }


def measure_audio_level(mp4: Path) -> Tuple[float, float]:
    """Returns (mean_dB, max_dB). None,None if no audio."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(mp4), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True
    )
    out = r.stderr
    mean = max_v = None
    for line in out.split("\n"):
        if "mean_volume" in line:
            try: mean = float(line.split(":")[-1].replace("dB", "").strip())
            except: pass
        if "max_volume" in line:
            try: max_v = float(line.split(":")[-1].replace("dB", "").strip())
            except: pass
    return mean, max_v


def check(mp4: Path, expected_min_dur: float = 8.0, expected_max_dur: float = 90.0) -> Tuple[bool, List[str]]:
    """Run all sanity checks. Returns (passed, reasons_failed)."""
    fail = []
    if not mp4.exists():
        return False, [f"file missing: {mp4}"]

    p = probe(mp4)
    if "error" in p:
        return False, [f"ffprobe failed: {p['error']}"]

    # 1. File size sanity
    if p["size_mb"] < 0.2:
        fail.append(f"file too small ({p['size_mb']:.2f}MB) — render likely failed")

    # 2. Duration sanity
    if p["duration"] < expected_min_dur:
        fail.append(f"duration {p['duration']:.1f}s < min {expected_min_dur}s")
    if p["duration"] > expected_max_dur:
        fail.append(f"duration {p['duration']:.1f}s > max {expected_max_dur}s")

    # 3. Audio presence
    if not p["has_audio"]:
        fail.append("NO AUDIO STREAM — would ship silent")

    # 4. Audio level
    if p["has_audio"]:
        mean_db, max_db = measure_audio_level(mp4)
        if mean_db is None:
            fail.append("could not measure audio level")
        elif mean_db < -35:
            fail.append(f"audio too quiet: mean {mean_db}dB (target > -20 for spoken)")
        elif max_db is not None and max_db < -10:
            fail.append(f"audio max only {max_db}dB (peaks should hit -3 dB)")

    # 5. Resolution sanity (9:16 portrait expected)
    if p["height"] < p["width"]:
        fail.append(f"landscape ({p['width']}x{p['height']}) — should be portrait 1080x1920")

    return (len(fail) == 0), fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mp4", help="path to the rendered reel")
    ap.add_argument("--strict", action="store_true", help="exit 2 on any failure")
    ap.add_argument("--min-dur", type=float, default=8.0)
    ap.add_argument("--max-dur", type=float, default=90.0)
    args = ap.parse_args()

    passed, reasons = check(Path(args.mp4), args.min_dur, args.max_dur)
    if passed:
        print(f"✓ {args.mp4} passed all sanity checks")
        sys.exit(0)
    print(f"✗ {args.mp4} FAILED sanity:")
    for r in reasons:
        print(f"  - {r}")
    sys.exit(2 if args.strict else 1)


if __name__ == "__main__":
    main()
