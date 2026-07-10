#!/usr/bin/env python3
"""
community_post.py — auto-post a daily YouTube Community post (text/poll).

WHY: Channels with daily Community activity rank higher in YT's "active channel" classifier.
Currently the FW channel has zero community posts — looks dormant between Shorts.

CAVEAT: YT's Community tab is gated to channels with 500+ subs (was 1000+ previously, dropped 2024).
At 1 sub, this tool will fail with quota/eligibility errors. It's still wired for when the
channel crosses the threshold — runs daily, logs the gating error gracefully, becomes
useful the moment eligibility opens.

Strategy: pick the most recent uploaded video, generate a thoughtful poll question about it
via Claude haiku, post as a Community post.
"""
from __future__ import annotations
import json, os, pathlib, sys
from datetime import datetime

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
LOG_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/community_post.log"))
SEEN_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/community_post_seen.json"))


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS_PATH.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def _claude_post(title: str):
    """Generate a 1-line community post question riffing on a recent video title."""
    import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
    cfg = json.loads(CREDS_PATH.read_text())
    client = Anthropic(api_key=cfg.get("anthropic_api_key", ""))
    prompt = f"""Generate a single YT Community post about this video title:

TITLE: "{title}"

Output: ONE engaging text post (10-25 words), ends with a question that invites comments.
Format: just the post text, no quotes, no preamble. Use 1-2 emojis max.
Example: "Imagine if you could only post one thing for the rest of your life — what would it be? "
"""
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip().strip('"').strip("'")


def _seen_load() -> set:
    if not SEEN_PATH.exists(): return set()
    try: return set(json.loads(SEEN_PATH.read_text()))
    except Exception: return set()


def _seen_save(s: set):
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(s)))


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_lines = [f"=== {datetime.now().isoformat()} community_post ==="]

    try:
        yt = _yt()
    except Exception as e:
        log_lines.append(f"  YT client setup failed: {e}")
        with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")
        return

    # Get the most recent uploaded video title (one that hasn't been used for a community post yet)
    seen = _seen_load()
    try:
        chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
        uploads_pl = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = yt.playlistItems().list(part="snippet,contentDetails",
                                      playlistId=uploads_pl, maxResults=10).execute()
    except Exception as e:
        log_lines.append(f"  failed to list recent videos: {e}")
        with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")
        return

    chosen = None
    for it in pl.get("items", []):
        vid_id = it["contentDetails"]["videoId"]
        if vid_id in seen: continue
        chosen = (vid_id, it["snippet"]["title"])
        break
    if not chosen:
        log_lines.append("  no unused recent video to post about — skipping")
        with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")
        return

    vid_id, title = chosen
    log_lines.append(f"  picked video: {vid_id} '{title[:60]}'")
    try:
        post_text = _claude_post(title)
    except Exception as e:
        log_lines.append(f"  Claude post-gen failed: {e}")
        with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")
        return
    log_lines.append(f"  post text: {post_text}")

    # NOTE: YT deprecated the community-posts API endpoint (activities.insert) in 2024.
    # As of 2026, community posts can ONLY be created via studio.youtube.com (manual click).
    # This tool generates the post TEXT via Claude — you copy-paste into Studio if you want.
    # See ~/PipelineCleanup/community_post_queue.md for the queue.
    queue_path = pathlib.Path(os.path.expanduser("~/PipelineCleanup/community_post_queue.md"))
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — about {vid_id}\n")
        f.write(f"**Video:** [{title[:80]}](https://youtube.com/shorts/{vid_id})\n\n")
        f.write(f"{post_text}\n")
    log_lines.append(f"   post text appended to {queue_path}")
    log_lines.append(f"  Manual step: studio.youtube.com → Community → New post → paste")
    seen.add(vid_id)
    _seen_save(seen)

    with open(LOG_PATH, "a") as f: f.write("\n".join(log_lines) + "\n")


if __name__ == "__main__":
    main()
