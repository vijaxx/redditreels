#!/usr/bin/env python3
"""
title_ab_test.py — Data-driven title rescue for underperforming videos.

Logic (2026-07-02 rewrite based on cross-platform view data):
  Pattern D "What happens when [ALLCAPS_KEYWORD]" = 35.4 avg views, #1 performer
  "You Won't Believe" / "Villain?" = 0.4 avg views, DEAD patterns

  RULES:
  1. Videos already on Pattern D → NEVER touch (don't break winners)
  2. Videos on DEAD patterns (You Won't Believe / Villain?) → ALWAYS swap to D
  3. Other videos with views < VIEWS_BELOW and age 24-72h → swap to D

  We ALWAYS swap TO Pattern D, never away from it.
"""
import os, sys, json, pathlib, re
from typing import Optional
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
SEEN = pathlib.Path.home() / "PipelineCleanup" / "title_ab_seen.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "title_ab.log"

AGE_HOURS_MIN = 24
AGE_HOURS_MAX = 72
VIEWS_BELOW = 20

# Patterns confirmed DEAD by data — always swap away from these
DEAD_PATTERNS = [
    r"(?i)^you won'?t believe",
    r"(?i)^villain\??",
    r"(?i)^VILLAIN\??",
]

# Pattern D signature — videos on this pattern are PROTECTED, never swapped
_PATTERN_D_RE = re.compile(r"(?i)^what happens when\b", re.IGNORECASE)


def _is_dead_pattern(title: str) -> bool:
    return any(re.match(p, title) for p in DEAD_PATTERNS)


def _is_pattern_d(title: str) -> bool:
    return bool(_PATTERN_D_RE.match(title))


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


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _load_seen() -> dict:
    if not SEEN.exists(): return {}
    try: return json.loads(SEEN.read_text())
    except: return {}


def _save_seen(s: dict):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(s, indent=2))


def _gen_pattern_d_title(orig_title: str, video_desc: str, api_key: str) -> Optional[str]:
    """Generate a Pattern D title for this video. Pattern D is always the target."""
    try:
        import sys as _lsys, pathlib as _lpath
        _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1]))
        from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Generate ONE YouTube Short title using Pattern D — the highest-performing pattern (35x more views than alternatives).

ORIGINAL TITLE: {orig_title}
DESCRIPTION (first 300 chars): {video_desc[:300]}

Pattern D formula (MANDATORY):
  "What happens when [ALLCAPS_CONFLICT_KEYWORD] [rest of scenario]? #Shorts"
  Examples:
    "What happens when ENTITLED boss fires the wrong person? #Shorts"
    "What happens when you EXPOSE a family LIAR? #Shorts"
    "What happens when MIL CROSSES the line? #Shorts"
    "What happens when BACKFIRE hits the BULLY? #Shorts"

Rules:
- MUST start with exactly "What happens when"
- MUST have at least one ALL-CAPS keyword (the conflict/drama word)
- 40-65 chars total (excluding #Shorts)
- End with " #Shorts"
- No emojis in title
- Must be honest to the story content

OUTPUT: just the new title string, nothing else."""
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=120,
            messages=[{"role": "user", "content": prompt}])
        result = msg.content[0].text.strip().strip('"').strip()
        # Validate it's actually Pattern D
        if not _is_pattern_d(result):
            _log(f"  LLM returned non-D title: '{result}' — prepending fix")
            result = "What happens when " + result.lstrip()
        return result
    except Exception as e:
        _log(f"  alt title gen failed: {e}")
        return None


def run():
    yt = _yt()
    seen = _load_seen()
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails,snippet", playlistId=uploads, maxResults=30).execute()
    now = datetime.now(timezone.utc)
    cands = []
    skipped_d = 0
    for it in pl.get("items", []):
        vid = it["contentDetails"]["videoId"]
        if vid in seen: continue
        pub = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
        age_h = (now - pub).total_seconds() / 3600
        title = it["snippet"]["title"]

        # Never touch Pattern D winners
        if _is_pattern_d(title):
            skipped_d += 1
            continue

        if AGE_HOURS_MIN <= age_h <= AGE_HOURS_MAX:
            cands.append((vid, age_h, title))

    if skipped_d:
        _log(f"  protected {skipped_d} Pattern-D titles (never swapped)")
    if not cands:
        _log("no title-rescue candidates"); return

    stats = yt.videos().list(part="statistics,snippet", id=",".join(c[0] for c in cands)).execute()
    items_by_id = {it["id"]: it for it in stats.get("items", [])}
    rotated = 0
    cfg = json.loads(CREDS.read_text())
    for vid, age_h, orig_title in cands:
        info = items_by_id.get(vid, {})
        v = int(info.get("statistics", {}).get("viewCount", 0))
        is_dead = _is_dead_pattern(orig_title)

        # Skip if not dead pattern AND has decent views
        if not is_dead and v >= VIEWS_BELOW:
            continue

        reason = "dead pattern" if is_dead else f"{v} views < {VIEWS_BELOW}"
        snip = info.get("snippet", {})
        desc = snip.get("description", "")
        alt = _gen_pattern_d_title(orig_title, desc, cfg.get("anthropic_api_key", ""))
        if not alt or alt == orig_title:
            _log(f"  {vid} ({reason}) — no alt title generated, skip"); continue
        if _is_pattern_d(orig_title) and not is_dead:
            _log(f"  {vid} — safety: already Pattern D, skip"); continue

        try:
            new_snip = {
                "title": alt[:100],
                "description": desc[:5000],
                "tags": snip.get("tags", [])[:30],
                "categoryId": snip.get("categoryId", "24"),
            }
            yt.videos().update(part="snippet", body={"id": vid, "snippet": new_snip}).execute()
            _log(f"   {vid} ({reason}, {age_h:.0f}h): '{orig_title[:35]}…' → '{alt[:55]}'")
            seen[vid] = {
                "orig": orig_title, "alt": alt,
                "reason": reason,
                "rotated_at": datetime.now().isoformat(),
                "views_at_swap": v,
            }
            rotated += 1
        except Exception as e:
            _log(f"   {vid} update failed: {e}")
    _save_seen(seen)
    _log(f"=== summary: rotated={rotated}/{len(cands)} (protected {skipped_d} Pattern-D) ===")


if __name__ == "__main__": run()
