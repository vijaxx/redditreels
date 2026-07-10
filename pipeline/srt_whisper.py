#!/usr/bin/env python3
"""
srt_whisper.py — REAL word-sync SRT via faster-whisper transcription.

Replaces the previous time-slice estimation. Transcribes the actual TTS-generated
narration mp3, producing per-word timestamps that match what's spoken EXACTLY.

Used by srt_export.py when faster_whisper is available; falls back otherwise.

Built 2026-06-03 overnight.
"""
import pathlib
from typing import List, Dict


def transcribe_to_word_timings(audio_path: pathlib.Path) -> List[Dict]:
    """Returns list of {word, start, end} via faster-whisper word_timestamps."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    import subprocess, tempfile, os
    # Convert to PCM WAV first — faster-whisper has AudioFifo issues with
    # concatenated MP3s (stinger + narration). WAV is always clean.
    wav_tmp = None
    transcribe_path = audio_path
    try:
        tmp_fd, wav_tmp = tempfile.mkstemp(suffix=".wav")
        os.close(tmp_fd)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(audio_path), "-ar", "16000", "-ac", "1", wav_tmp],
            check=True, capture_output=True
        )
        transcribe_path = pathlib.Path(wav_tmp)
    except Exception:
        transcribe_path = audio_path  # fall back to original if conversion fails
    try:
        model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(transcribe_path), word_timestamps=True,
                                         vad_filter=False, beam_size=1)
        out = []
        for s in segments:
            for w in (s.words or []):
                out.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        return out
    finally:
        if wav_tmp:
            try: os.unlink(wav_tmp)
            except Exception: pass


def whisper_srt(audio_path: pathlib.Path, max_chars: int = 42, max_secs: float = 2.5) -> str:
    """Generate SRT from real whisper transcription. Empty if whisper unavailable."""
    words = transcribe_to_word_timings(audio_path)
    if not words: return ""
    # Reuse the chunking logic from srt_export
    chunks = []
    cur_words = []
    cur_start = None
    for w in words:
        if cur_start is None: cur_start = w["start"]
        cur_words.append(w)
        cur_text = " ".join(x["word"] for x in cur_words)
        duration = w["end"] - cur_start
        ends_punct = cur_words[-1]["word"][-1:] in ".!?"
        if len(cur_text) > max_chars or duration >= max_secs or ends_punct:
            chunks.append({"start": cur_start, "end": w["end"], "text": cur_text})
            cur_words = []; cur_start = None
    if cur_words:
        chunks.append({"start": cur_start, "end": cur_words[-1]["end"],
                        "text": " ".join(x["word"] for x in cur_words)})

    def fmt_ts(t):
        h = int(t // 3600); m = int((t % 3600) // 60)
        s = t % 60; ms = int((s - int(s)) * 1000); s = int(s)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    out = []
    for i, c in enumerate(chunks, 1):
        out.append(f"{i}\n{fmt_ts(c['start'])} --> {fmt_ts(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: srt_whisper.py <audio_path>"); sys.exit(1)
    print(whisper_srt(pathlib.Path(sys.argv[1])))
