#!/usr/bin/env python3
"""Tries to be an early commenter on whatever's trending in the same niche.
Each morning, finds the 5 most-watched Shorts from the last 6 hours matching a
few search terms and posts an early comment from the channel account -- skips
anything with 50+ comments already (won't stand out there) and anything from
the channel itself. Capped at 5 comments a run."""
import os, sys, json, pathlib, random
from datetime import datetime, timezone, timedelta

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
SEEN = pathlib.Path.home() / "PipelineCleanup" / "niche_first_seen.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "niche_first.log"

SEARCH_TERMS = [
    "reddit story shorts", "aita shorts", "tifu shorts",
    "reddit storytime", "petty revenge story",
]
MAX_PER_RUN = 5
COMMENTS_THRESHOLD = 50  # skip if already >this many comments (we'd be invisible)


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"])
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def _load_seen() -> set:
    if not SEEN.exists(): return set()
    try: return set(json.loads(SEEN.read_text()))
    except: return set()


def _save_seen(s):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(s)))


def _gen_comment(video_title: str, api_key: str) -> str:
    """Claude writes a thoughtful first-commenter style remark."""
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Write a thoughtful FIRST COMMENT for this YouTube Short. Style: like a real viewer who watched + had an authentic reaction.

VIDEO TITLE: {video_title}

Rules:
- 8-25 words
- Specific to the title (NOT generic — don't say "great video")
- Add value: a related insight, a question, OR a counterpoint
- Conversational tone, lowercase OK
- ONE emoji max (or none)
- NO promotional content (no @mentions, no "check out my channel")
- DO NOT use sponsored phrases

Output ONLY the comment, no preamble."""
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=100,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text.strip().strip('"').strip()
    except Exception as e:
        return None


def run(execute: bool = False):
    yt = _yt()
    cfg = json.loads(CREDS.read_text())
    seen = _load_seen()
    candidates = []

    # Search across niche terms
    for term in random.sample(SEARCH_TERMS, min(3, len(SEARCH_TERMS))):
        try:
            r = yt.search().list(
                part="snippet", q=term, type="video", videoDuration="short",
                order="date", publishedAfter=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
                maxResults=10
            ).execute()
            for it in r.get("items", []):
                vid = it["id"]["videoId"]
                ch = it["snippet"]["channelId"]
                if ch == CHANNEL_ID: continue   # don't comment on own
                if vid in seen: continue
                candidates.append({"video_id": vid, "channel_id": ch,
                                    "title": it["snippet"]["title"]})
        except Exception as e:
            _log(f"  search '{term}' fail: {e}")
    if not candidates:
        _log("no candidates"); return

    # Score by view + comment count
    stats = yt.videos().list(part="statistics", id=",".join(c["video_id"] for c in candidates[:50])).execute()
    metas = {it["id"]: it.get("statistics", {}) for it in stats.get("items", [])}
    enriched = []
    for c in candidates:
        m = metas.get(c["video_id"], {})
        v = int(m.get("viewCount", 0))
        cmts = int(m.get("commentCount", 0))
        if cmts > COMMENTS_THRESHOLD: continue  # already crowded
        if v < 100: continue  # too small to bother
        enriched.append({**c, "views": v, "comments": cmts})
    enriched.sort(key=lambda c: c["views"], reverse=True)

    posted = 0
    for c in enriched[:MAX_PER_RUN]:
        comment = _gen_comment(c["title"], cfg.get("anthropic_api_key", ""))
        if not comment:
            _log(f"   no comment generated for {c['video_id']}"); continue
        _log(f"  {'POST' if execute else 'WOULD-POST'} v={c['views']} cmts={c['comments']}  "
             f"→ {c['title'][:50]}  |  comment: {comment[:80]}")
        if execute:
            try:
                yt.commentThreads().insert(part="snippet", body={
                    "snippet": {"videoId": c["video_id"],
                                 "topLevelComment": {"snippet": {"textOriginal": comment}}}
                }).execute()
                seen.add(c["video_id"])
                posted += 1
            except Exception as e:
                _log(f"    post fail: {e}")
    _save_seen(seen)
    _log(f"=== summary: {'posted' if execute else 'would-post'} {posted}/{len(enriched[:MAX_PER_RUN])} ===")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    run(execute=args.execute)
