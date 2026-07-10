#!/usr/bin/env python3
"""Generate edge-tts narration with per-word timings (WordBoundary)."""
import asyncio, json, os, pathlib, sys
import edge_tts

import random as _rand
# Voice rotation pool — 5 distinct US neural voices. Random pick per fire = fresher feel,
# broader appeal, less "every video sounds the same AI" perception. All free edge-tts.
VOICE_POOL = [
    "en-US-AndrewNeural",         # casual male, baseline        — 100% YT/Rum, 91% green
    "en-US-ChristopherNeural",    # warmer male, narration-style — 90% YT, 100% Rum, 90% green
    "en-US-BrianNeural",          # younger male, conversational — 100% YT, 75% Rum early-period
    "en-US-EmmaNeural",           # casual female, storytime     — 100% YT/Rum, 88% green
    "en-US-AvaNeural",            # confident female, news-style — 100% YT/Rum, 100% green
    # accent variety
    "en-GB-RyanNeural",           # British male  — 100% YT, 92% Rum, 92% green
    "en-AU-NatashaNeural",        # Australian female — 100% YT/Rum, 100% green (top performer)
    "en-IE-EmilyNeural",          # Irish female  — 84% YT (early failures), 92% Rum, 92% green
    "en-CA-LiamNeural",           # Canadian male — 100% YT/Rum, 100% green (top performer)
    # en-US-AndrewMultilingualNeural removed 2026-07-01: 77% green rate (lowest of pool),
    # 2/9 runs triggered yellow ad-safety flag. All other voices ≥88% green.
]
VOICE = __import__("os").environ.get("RR_VOICE") or _rand.choice(VOICE_POOL)
RATE  = "+8%"

import os, pathlib
ROOT = pathlib.Path(os.environ.get('RR_ROOT', os.path.expanduser('~/RedditReels')))
WORK = pathlib.Path(os.environ.get('RR_WORK', str(ROOT / 'output')))
WORK.mkdir(parents=True, exist_ok=True)

IN  = pathlib.Path((WORK / "script.json"))
OUT_MP3 = pathlib.Path((WORK / "narration.mp3"))
OUT_TIMINGS = pathlib.Path((WORK / "timings.json"))

async def _synth_once(text, mp3_path):
    """Single TTS attempt. Returns timings list (may be truncated if stream cuts off)."""
    comm = edge_tts.Communicate(text, voice=VOICE, rate=RATE, boundary="WordBoundary")
    timings = []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                end   = (chunk["offset"] + chunk["duration"]) / 1e7
                timings.append({"word": chunk["text"], "start": start, "end": end})
    return timings


async def _synth_chunked(text, mp3_path):
    """Fallback: split text into sentence chunks, TTS each separately, concat audio.
    Reduces per-call failure surface — if one sentence fails, retry just that one."""
    import re as _re, subprocess as _sp, tempfile as _tf, pathlib as _p
    # Split on sentence boundaries while keeping the delimiter
    parts = _re.split(r'(?<=[.!?])\s+', text.strip())
    parts = [p for p in parts if p.strip()]
    if len(parts) <= 1:
        return await _synth_once(text, mp3_path)

    tmp = _p.Path(_tf.mkdtemp(prefix="tts_chunk_"))
    all_timings = []
    chunk_mp3s = []
    cumulative_offset_s = 0.0
    for i, part in enumerate(parts):
        chunk_mp3 = tmp / f"c{i:03d}.mp3"
        chunk_timings = None
        for retry in range(3):
            try:
                chunk_timings = await _synth_once(part, chunk_mp3)
                if chunk_timings and len(chunk_timings) >= len(part.split()) * 0.6:
                    break
            except Exception:
                continue
        if not chunk_timings:
            print(f"  [synth_chunked] chunk {i} ('{part[:40]}...') failed all retries", file=sys.stderr)
            continue
        # Shift timings by cumulative offset
        for t in chunk_timings:
            all_timings.append({
                "word": t["word"],
                "start": t["start"] + cumulative_offset_s,
                "end":   t["end"]   + cumulative_offset_s,
            })
        # Probe chunk duration via ffprobe
        try:
            dur = float(_sp.check_output(
                ["ffprobe","-v","error","-show_entries","format=duration",
                 "-of","default=noprint_wrappers=1:nokey=1", str(chunk_mp3)]
            ).strip())
            cumulative_offset_s += dur
        except Exception:
            cumulative_offset_s += chunk_timings[-1]["end"] - chunk_timings[0]["start"]
        chunk_mp3s.append(chunk_mp3)

    # Concat all chunks via ffmpeg
    if chunk_mp3s:
        concat_list = tmp / "list.txt"
        concat_list.write_text("\n".join(f"file '{c}'" for c in chunk_mp3s))
        _sp.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0",
                 "-i", str(concat_list), "-c","copy", str(mp3_path)], check=True)
    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)
    return all_timings


async def synth(text, expected_words: int):
    """TTS with verify-and-retry. If first attempt captures <85% of expected words
    (sign of edge-tts mid-stream truncation), retry up to 2 more times. If still bad,
    fall back to chunked synthesis (one TTS call per sentence)."""
    for attempt in range(1, 4):
        try:
            timings = await _synth_once(text, OUT_MP3)
        except Exception as e:
            print(f"[voice_gen] attempt {attempt} EXCEPTION: {e}", file=sys.stderr)
            continue
        captured_ratio = len(timings) / max(1, expected_words)
        if captured_ratio >= 0.85:
            return timings
        print(f"[voice_gen] attempt {attempt}: captured {len(timings)}/{expected_words} words "
              f"({captured_ratio:.1%}) — retrying", file=sys.stderr)

    # All retries truncated → chunked fallback (per-sentence synthesis)
    print(f"[voice_gen] falling back to per-sentence chunked synthesis", file=sys.stderr)
    timings = await _synth_chunked(text, OUT_MP3)
    final_ratio = len(timings) / max(1, expected_words)
    if final_ratio < 0.75:
        raise RuntimeError(f"TTS captured only {len(timings)}/{expected_words} words "
                           f"({final_ratio:.1%}) even after chunked fallback")
    return timings


def _prepend_stinger(mp3_path: pathlib.Path, timings: list, stinger_path: pathlib.Path) -> list:
    """Prepend stinger SFX before the narration. Shifts timings forward.
    Effect: attention-grabbing audio cue at video start before voice."""
    import subprocess as _sp
    if not stinger_path.exists(): return timings
    # Probe stinger duration
    try:
        stinger_dur = float(_sp.check_output([
            "ffprobe","-v","error","-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1", str(stinger_path)
        ]).strip())
    except Exception:
        return timings
    # Concat: stinger + narration  → temp file → replace original
    tmp = mp3_path.with_suffix(".sting.mp3")
    list_file = mp3_path.with_suffix(".list.txt")
    list_file.write_text(f"file '{stinger_path}'\nfile '{mp3_path}'\n")
    try:
        _sp.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0",
                 "-i", str(list_file), "-c","copy", str(tmp)], check=True)
        tmp.replace(mp3_path)
    except Exception:
        return timings
    finally:
        try: list_file.unlink()
        except Exception: pass
    # Shift all timings forward by stinger_dur
    return [{"word": t["word"], "start": t["start"]+stinger_dur, "end": t["end"]+stinger_dur} for t in timings]


def main():
    script = json.load(open(IN))
    text = script["narration"]
    expected_words = len(text.split())
    timings = asyncio.run(synth(text, expected_words))
    # Prepend stinger SFX if available (attention-grabber before voice starts)
    stinger = ROOT / "assets" / "stinger.mp3"
    if stinger.exists():
        timings = _prepend_stinger(OUT_MP3, timings, stinger)
        print(f"[voice_gen] stinger prepended (timings shifted by {timings[0]['start'] if timings else 0:.2f}s)")
    json.dump(timings, open(OUT_TIMINGS, "w"), indent=2)
    dur = timings[-1]["end"] if timings else 0
    ratio = len(timings) / max(1, expected_words)
    print(f"[voice_gen] voice={VOICE}  {len(timings)}/{expected_words} words ({ratio:.1%}) synced, total {dur:.2f}s")
    print(f"  mp3: {OUT_MP3.stat().st_size/1024:.1f} KB")

if __name__ == "__main__":
    main()
