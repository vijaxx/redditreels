#!/usr/bin/env python3
"""
srt_export.py — generate an SRT caption file from word timings.

YT favors videos with separately-uploaded captions in search + recommendation.
Burned-in captions help readability but aren't indexed by YT's transcript engine.
Adding an SRT file via captions.insert exposes the full transcript text to YT's
search indexer — meaningful SEO unlock for discoverability.

Strategy: group words into 1.5-2.5s phrases (natural reading pace), output SRT.
"""
from __future__ import annotations
import json, os, pathlib, sys


def timings_to_srt(timings: list, max_chars_per_line: int = 42,
                   max_chunk_secs: float = 2.5) -> str:
    """Convert per-word timings into SRT chunks suitable for YT caption upload."""
    if not timings: return ""
    chunks = []
    cur_words, cur_start = [], None
    for w in timings:
        if cur_start is None:
            cur_start = w["start"]
        cur_words.append(w["word"])
        cur_text = " ".join(cur_words)
        # Break on chunk-length OR sentence-ending punctuation OR ~2.5s duration
        duration = w["end"] - cur_start
        ends_punct = (cur_words[-1] or "")[-1:] in ".!?"
        if len(cur_text) > max_chars_per_line or duration >= max_chunk_secs or ends_punct:
            chunks.append({"start": cur_start, "end": w["end"], "text": cur_text})
            cur_words, cur_start = [], None
    if cur_words and cur_start is not None:
        chunks.append({"start": cur_start, "end": timings[-1]["end"], "text": " ".join(cur_words)})

    def fmt_srt_time(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, c in enumerate(chunks, 1):
        lines.append(str(i))
        lines.append(f"{fmt_srt_time(c['start'])} --> {fmt_srt_time(c['end'])}")
        lines.append(c["text"])
        lines.append("")
    return "\n".join(lines)


def write_srt_from_work(work_dir: pathlib.Path) -> pathlib.Path | None:
    """Write work_dir/captions.srt. Tries whisper transcription first (most accurate),
    falls back to estimated timings.json if whisper unavailable.
    2026-06-03 overnight: real whisper-aligned captions instead of word-position estimate."""
    out = work_dir / "captions.srt"
    # Try whisper first
    audio = work_dir / "narration.mp3"
    if audio.exists():
        try:
            from srt_whisper import whisper_srt
            srt = whisper_srt(audio)
            if srt:
                out.write_text(srt)
                print(f"[srt_export] used whisper transcription ({len(srt)} chars)")
                return out
        except Exception as e:
            print(f"[srt_export] whisper unavailable, fallback to timings: {e}")
    # Fallback to old method
    timings_path = work_dir / "timings.json"
    if not timings_path.exists(): return None
    timings = json.loads(timings_path.read_text())
    srt = timings_to_srt(timings)
    if not srt: return None
    out.write_text(srt)
    return out


if __name__ == "__main__":
    work = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/RedditReels/output"))
    p = write_srt_from_work(work)
    if p:
        print(f"wrote {p}  ({p.stat().st_size} B)")
    else:
        print("no timings.json found")
