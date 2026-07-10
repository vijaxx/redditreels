#!/usr/bin/env python3
"""
multilingual_captions.py — generate Hindi + Spanish captions for each YT video.

After upload, translates the English SRT to Hindi and Spanish (via Claude) and
uploads them as additional caption tracks. YT search picks up multilingual
captions = potential 2-3x discovery surface.

Usage (post-upload, called from redditreels.py):
    add_translations(yt_video_id, english_srt_path)

Built 2026-06-03 overnight.
"""
import os, sys, json, pathlib, re, tempfile
from typing import List

CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"

# Target languages: Hindi (huge India market) + Spanish (huge LatAm market)
# YT language codes per https://developers.google.com/youtube/v3/docs/captions
LANGS = [
    {"code": "hi", "name": "Hindi"},
    {"code": "es", "name": "Spanish"},
]


def parse_srt(srt_text: str) -> List[dict]:
    """Returns list of {idx, time, text}."""
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    out = []
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) < 3: continue
        try:
            out.append({"idx": int(lines[0]),
                        "time": lines[1],
                        "text": " ".join(lines[2:])})
        except ValueError:
            continue
    return out


def serialize_srt(entries: List[dict]) -> str:
    return "\n\n".join(f"{e['idx']}\n{e['time']}\n{e['text']}" for e in entries) + "\n"


def translate_batch(texts: List[str], target_lang: str, api_key: str) -> List[str]:
    """Claude translates a batch of texts → list of translations same order."""
    import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
    client = Anthropic(api_key=api_key)
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = f"""Translate these {len(texts)} English caption lines to {target_lang}. Preserve numbering. Keep informal, conversational tone (these are YouTube Shorts captions). Don't translate proper names or hashtags. Match line count exactly.

ENGLISH:
{numbered}

Output ONLY the numbered translations, one per line. No preamble, no notes."""
    msg = client.messages.create(
        model="claude-haiku-4-5", max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    lines = []
    for line in text.split("\n"):
        m = re.match(r"^\s*\d+[\.\)]\s*(.+)$", line)
        if m:
            lines.append(m.group(1).strip())
    # Fallback: if not enough lines, pad with original
    while len(lines) < len(texts):
        lines.append(texts[len(lines)])
    return lines[:len(texts)]


def add_translations(yt_video_id: str, english_srt_path: pathlib.Path, log=None) -> dict:
    """Translate English SRT → Hindi + Spanish → upload to YT video.
    Returns {hi: success_bool, es: success_bool, errors: [...]}.
    """
    def _l(m):
        if log: log.info(f"  [multilingual] {m}")
        else: print(f"  [multilingual] {m}")

    result = {}
    if not english_srt_path.exists():
        return {"error": "english srt missing"}

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        cfg = json.loads(CREDS.read_text())
        creds = Credentials(
            token=None, refresh_token=cfg["youtube_refresh_token_broad"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
            scopes=["https://www.googleapis.com/auth/youtube",
                    "https://www.googleapis.com/auth/youtube.force-ssl"])
        creds.refresh(Request())
        yt = build("youtube", "v3", credentials=creds)
    except Exception as e:
        return {"error": f"yt auth: {e}"}

    en_entries = parse_srt(english_srt_path.read_text())
    if not en_entries:
        return {"error": "srt parse failed"}

    en_texts = [e["text"] for e in en_entries]

    for lang in LANGS:
        try:
            _l(f"translating {len(en_texts)} lines → {lang['name']}")
            translated = translate_batch(en_texts, lang["name"], cfg.get("anthropic_api_key", ""))
            translated_entries = [{**e, "text": translated[i]} for i, e in enumerate(en_entries)]
            tmp_srt = pathlib.Path(tempfile.mkdtemp()) / f"{lang['code']}.srt"
            tmp_srt.write_text(serialize_srt(translated_entries))
            yt.captions().insert(part="snippet", body={
                "snippet": {"videoId": yt_video_id, "language": lang["code"],
                            "name": lang["name"], "isDraft": False}
            }, media_body=MediaFileUpload(str(tmp_srt), mimetype="application/octet-stream")).execute()
            _l(f"  ✓ {lang['name']} captions uploaded")
            result[lang["code"]] = True
        except Exception as e:
            _l(f"  ✗ {lang['name']} failed: {e}")
            result[lang["code"]] = False
            result.setdefault("errors", []).append(f"{lang['code']}: {e}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: multilingual_captions.py <yt_video_id> <english_srt_path>")
        sys.exit(1)
    print(json.dumps(add_translations(sys.argv[1], pathlib.Path(sys.argv[2])), indent=2))
