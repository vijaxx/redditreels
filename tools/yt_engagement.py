#!/usr/bin/env python3
"""
yt_engagement.py — post engagement-bait comments after YT upload.

Comments anchor as the channel-owner's voice → YT surfaces them prominently to
the first 100 viewers. Drives reply chains = engagement signal = wider distribution.

Requires the `youtube.force-ssl` OAuth scope, which is NOT in the upload-only
refresh token. The function GRACEFULLY skips if scope is missing — no errors
thrown into the orchestrator. To enable: run
  python3 ~/RedditReels/tools/check_monetization.py --auth
which captures the broader-scope refresh token (also used here).
"""
from __future__ import annotations
import logging
import random
import time
from typing import Optional

log = logging.getLogger("rr.engagement")

ENGAGEMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

# Reddit-story-flavored prompts. One picked at random per upload (deterministic
# enough with the video_id seed to give consistent per-video pin).
# 2026-07-01 — "Am I The Villain?" series alignment: the reel opens
# "You decide — am I the villain?" and the caption signs off "⚖️ Verdict in the
# comments — you judge." The pinned comment now COMPLETES that verdict ritual so
# the pinned CTA matches the hook/title/caption — one recognizable, subscribable
# format. Kept a few follow/part-2 lines (subscriber-growth lever) but tied them
# to the "new villain every day" series frame.
RR_PROMPTS = [
    # Verdict-bait — the series ritual: drives reply chains (viewers arguing verdicts)
    "⚖️ You decide: villain or NOT the villain? Drop your verdict 👇",
    "Verdict time 👇 was OP the villain here, or totally justified?",
    "Am I the villain? YOU be the judge — comment your verdict ⚖️",
    "🔴 Villain or 🟢 justified? Cast your verdict below 👇",
    "Jury's out. Villain or not? Comment your verdict — I read every one.",
    "Guilty or innocent? Drop your one-word verdict 💬",
    # Follow-bait tied to the series — drives subscribers ("a new villain daily")
    "Follow for tomorrow's case — a new villain every day. 👇",
    "Follow + comment 'VERDICT' and I'll post what happened next.",
    "🔔 New villain on trial daily. Follow so you don't miss the next case.",
]

SS_PROMPTS = [
    "Did you know this before? 🤯",
    "What other facts blow your mind? 👇",
    "Save this — you'll want to remember it.",
    "Drop a 🤯 if this changed how you think.",
    "Mind. Officially. Blown.",
    "Tag someone who needs to hear this.",
    "What's the wildest fact YOU know? Comment 👇",
    "Hit follow for daily mind-benders.",
    "How did NOBODY teach us this?",
    "Comment 🧠 if you learned something.",
]


def _build_force_ssl_client(cfg: dict):
    """Build a YouTube client with the force-ssl scope. Raises if not available."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    rt = cfg.get("youtube_refresh_token_broad") or cfg.get("youtube_refresh_token_force_ssl")
    if not rt:
        raise RuntimeError("no broad-scope refresh token (run tools/check_monetization.py --auth)")
    creds = Credentials(
        token=None, refresh_token=rt,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"],
        client_secret=cfg["youtube_client_secret"],
        # Scopes MUST match what was granted via check_monetization.py --auth,
        # i.e. ['youtube', 'youtube.force-ssl']. Listing scopes that weren't
        # granted (readonly, youtubepartner) causes invalid_scope on refresh.
        scopes=["https://www.googleapis.com/auth/youtube",
                ENGAGEMENT_SCOPE],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def pin_engagement_comment(video_id: str, prompts: list, cfg: dict,
                           seed: Optional[str] = None) -> Optional[str]:
    """Post (and best-effort heart) an engagement comment on `video_id`.

    Returns the commentThread ID on success, None if scope was missing (graceful skip)
    or the comment failed for any other reason (does NOT raise — engagement is
    a bonus, not a requirement for the pipeline).
    """
    try:
        yt = _build_force_ssl_client(cfg)
    except Exception as e:
        log.info(f"[yt_engagement] skip — broad-scope OAuth not granted ({e.__class__.__name__})")
        return None

    rng = random.Random(seed or video_id)
    text = rng.choice(prompts)
    try:
        resp = yt.commentThreads().insert(part="snippet", body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }).execute()
        thread_id = resp["id"]
        log.info(f"[yt_engagement] posted comment {thread_id!r}: {text!r}")
        # Best-effort heart from channel owner — visibility boost
        try:
            yt.comments().setModerationStatus(id=thread_id, moderationStatus="published").execute()
        except Exception:
            pass
        return thread_id
    except Exception as e:
        log.warning(f"[yt_engagement] comment insert failed: {e}")
        return None
