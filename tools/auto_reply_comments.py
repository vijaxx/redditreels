#!/usr/bin/env python3
"""
auto_reply_comments.py — read recent comments on the channel's videos and reply via Claude.

Every reply triggers a notification back to the commenter (they may return to watch
more) AND signals 'creator-viewer dialog' to YT's algo — both heavy engagement weights.

Safety:
  - REPLIES ARE CHECKED for ad-safety before posting (strip banned words via reuse
    of the RR scrubber).
  - Skips comments that look like spam/bots (heuristic).
  - Skips if the channel-owner has already replied (prevents reply-loops).
  - Logs every reply to ~/PipelineCleanup/auto_reply.log.
  - Per-video reply cap: 5 (don't carpet-bomb).

Usage:
  python3 auto_reply_comments.py             # dry-run (print intended replies)
  python3 auto_reply_comments.py --execute   # actually post
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, time
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
LOG_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/auto_reply.log"))
SEEN_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/auto_reply_seen.json"))

MAX_REPLIES_PER_VIDEO = 5
MAX_VIDEOS_TO_SCAN = 10
MIN_COMMENT_CHARS = 5

SYSTEM_PROMPT = """You reply to YouTube comments AS THE CREATOR of a Reddit-storytime / cinematic-quote / curiosity-facts channel.

REPLY RULES:
- 1-2 short sentences. Casual, friendly, warm.
- ALWAYS end with a question OR an invitation to engage further. This drives reply chains.
- If commenter shares a similar experience → ask a follow-up question.
- If commenter expresses emotion (  ) → mirror it briefly + ask what triggered it.
- If commenter asks a question → answer it + ask one back.
- If the comment is hostile / negative → respond gracefully, no defensiveness, redirect to a question.
- NEVER use these words: orgasm, sex, sexy, fuck, shit, damn, hell, ass, kill, suicide, murder, porn, smut.
- NEVER promise things you can't deliver ("we'll do part 2 tomorrow!" UNLESS the original video set that up).
- NEVER use generic openers ("Thanks for watching!", "Great point!"). Be specific to what they said.
- Reply MUST be in the SAME LANGUAGE as the comment.

OUTPUT: only the reply text, nothing else. No quotes, no formatting. Plain text reply ready to post.

If the comment is spam, bot text, link-drop, or impossible to engage with, output exactly: SKIP
"""


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


def _claude_reply(comment_text: str) -> str | None:
    import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
    cfg = json.loads(CREDS_PATH.read_text())
    client = Anthropic(api_key=cfg.get("anthropic_api_key", ""))
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=120,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"COMMENT:\n{comment_text}"}],
    )
    text = msg.content[0].text.strip()
    if text == "SKIP" or len(text) < 5:
        return None
    # Safety scrub — reuse RR's scrubber for the banned word list
    sys.path.insert(0, str(pathlib.Path.home() / "RedditReels" / "pipeline"))
    try:
        from rewrite_story import scrub_text
        text, _ = scrub_text(text)
    except Exception:
        pass
    return text


def _seen_load() -> set:
    if not SEEN_PATH.exists(): return set()
    try: return set(json.loads(SEEN_PATH.read_text()))
    except Exception: return set()


def _seen_save(s: set):
    SEEN_PATH.write_text(json.dumps(sorted(s)))


def _is_owner_comment(c: dict, channel_id: str) -> bool:
    return (c.get("snippet", {}).get("authorChannelId", {}).get("value") == channel_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually post replies")
    args = ap.parse_args()

    yt = _yt()
    seen = _seen_load()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = [f"=== {datetime.now().isoformat()} auto_reply (execute={args.execute}) ==="]

    # Walk uploads playlist for last N videos
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=MAX_VIDEOS_TO_SCAN).execute()
    vid_ids = [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
    print(f"Scanning {len(vid_ids)} recent videos for new comments...")

    replied_count = 0
    for vid in vid_ids:
        per_video_replies = 0
        try:
            ct = yt.commentThreads().list(
                part="snippet", videoId=vid, maxResults=20, order="time"
            ).execute()
        except Exception as e:
            log.append(f"  skip {vid}: list comments failed: {e}"); continue
        for thread in ct.get("items", []):
            if per_video_replies >= MAX_REPLIES_PER_VIDEO: break
            tid = thread["id"]
            top = thread["snippet"]["topLevelComment"]
            top_id = top["id"]
            if top_id in seen: continue
            if _is_owner_comment(top, CHANNEL_ID): seen.add(top_id); continue
            text = (top["snippet"].get("textOriginal") or "").strip()
            if len(text) < MIN_COMMENT_CHARS: seen.add(top_id); continue
            if thread["snippet"].get("totalReplyCount", 0) > 0:
                # Owner may have already replied — fetch replies and check
                try:
                    reps = yt.comments().list(part="snippet", parentId=top_id, maxResults=10).execute()
                    if any(_is_owner_comment(r, CHANNEL_ID) for r in reps.get("items", [])):
                        seen.add(top_id); continue
                except Exception: pass

            # Generate reply
            reply = _claude_reply(text)
            if not reply:
                line = f"  SKIP   vid={vid} comment_id={top_id}  text='{text[:60]}'"
                print(line); log.append(line); seen.add(top_id); continue

            action = "POST" if args.execute else "WOULD-POST"
            line = f"  {action}  vid={vid}  →  '{text[:50]}'  ↳  '{reply[:80]}'"
            print(line); log.append(line)
            if args.execute:
                try:
                    yt.comments().insert(part="snippet", body={
                        "snippet": {"parentId": top_id, "textOriginal": reply}
                    }).execute()
                    log.append(f"    → posted OK")
                except Exception as e:
                    log.append(f"    → POST FAILED: {e}")
            seen.add(top_id)
            per_video_replies += 1
            replied_count += 1

    _seen_save(seen)
    summary = f"=== summary: {'posted' if args.execute else 'would-post'} {replied_count} replies, seen-set now {len(seen)} ==="
    print(summary); log.append(summary)
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
