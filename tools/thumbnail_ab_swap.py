#!/usr/bin/env python3
"""Simple thumbnail A/B test: if a video has under 5 views 6 hours after
upload and an alternate frame is available (face-detected, or just the
30%/70% timestamp), swap the thumbnail once. Only ever swaps a given video
one time -- deliberately conservative."""
import os, sys, json, pathlib, subprocess, tempfile
from typing import Optional
from datetime import datetime, timedelta, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
SEEN = pathlib.Path.home() / "PipelineCleanup" / "thumb_swap_seen.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "thumb_swap.log"

# Only swap thumbnails on videos that meet ALL of these
AGE_HOURS_MIN = 6
AGE_HOURS_MAX = 36
VIEWS_BELOW = 5  # if fewer than this views in the age window → swap


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
    with open(LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _load_seen() -> set:
    if not SEEN.exists(): return set()
    try: return set(json.loads(SEEN.read_text()))
    except: return set()


def _save_seen(s: set):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(s)))


def _local_mp4_for_yt(yt_id: str) -> Optional[pathlib.Path]:
    """Find the local rendered MP4 for a given YT video ID by scanning logs."""
    up = pathlib.Path.home() / "RedditReels/logs/uploads.jsonl"
    if not up.exists(): return None
    for line in up.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        if e.get("yt_video_id") == yt_id:
            # Look in processing dir
            ts = e.get("ts")
            if ts:
                d = pathlib.Path.home() / f"RedditReels/processing/{ts}"
                if d.exists():
                    mp4s = list(d.glob("*final*.mp4")) or list(d.glob("*.mp4"))
                    if mp4s: return mp4s[0]
    return None


def extract_alt_thumb(mp4_path: pathlib.Path, target_t: float) -> Optional[pathlib.Path]:
    """Extract frame at target_t seconds → /tmp/alt_thumb_<vid_id>.jpg"""
    out = pathlib.Path(tempfile.mkdtemp()) / "alt.jpg"
    cmd = ["ffmpeg", "-y", "-ss", f"{target_t:.2f}", "-i", str(mp4_path),
           "-vframes", "1", "-q:v", "2", str(out)]
    r = subprocess.run(cmd, capture_output=True)
    return out if (r.returncode == 0 and out.exists() and out.stat().st_size > 1000) else None


def swap_yt_thumbnail(yt, video_id: str, thumb_path: pathlib.Path) -> bool:
    try:
        from googleapiclient.http import MediaFileUpload
        yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
        ).execute()
        return True
    except Exception as e:
        _log(f"  thumbnail upload failed for {video_id}: {e}")
        return False


def run():
    # Retired: this only swaps YouTube thumbnails, and YouTube is a small slice of views for
    # this channel and unmonetizable here -- most views come from Facebook Reels, which
    # doesn't use a swappable thumbnail at all. Every run was spending an LLM call and a YT
    # OAuth refresh optimizing the platform that can't pay. Disabled by default; set
    # RR_ENABLE_THUMB_SWAP=1 to bring it back (e.g. once YT monetization is unlocked).
    if os.environ.get("RR_ENABLE_THUMB_SWAP") != "1":
        _log("RETIRED: thumbnail_ab_swap is YouTube-only (~5% of views, unmonetizable) — "
             "no-op. Set RR_ENABLE_THUMB_SWAP=1 to re-enable.")
        return
    yt = _yt()
    seen = _load_seen()
    # Walk recent uploads
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails,snippet", playlistId=uploads, maxResults=30).execute()
    now = datetime.now(timezone.utc)
    cands = []
    for it in pl.get("items", []):
        vid = it["contentDetails"]["videoId"]
        if vid in seen: continue
        pub = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
        age_h = (now - pub).total_seconds() / 3600
        if AGE_HOURS_MIN <= age_h <= AGE_HOURS_MAX:
            cands.append((vid, age_h, it["snippet"]["title"]))
    if not cands:
        _log("no candidates in age window")
        return
    # Get view counts
    stats = yt.videos().list(part="statistics", id=",".join(c[0] for c in cands)).execute()
    views_by = {it["id"]: int(it["statistics"].get("viewCount", 0)) for it in stats.get("items", [])}
    swapped = 0
    for vid, age_h, title in cands:
        v = views_by.get(vid, 0)
        if v >= VIEWS_BELOW:
            continue
        # Find local MP4 → extract alt frame
        mp4 = _local_mp4_for_yt(vid)
        if not mp4:
            _log(f"  {vid} ({v}v, {age_h:.1f}h) — no local mp4, skip")
            continue
        # Probe duration, pick 60% timestamp
        try:
            dur = float(subprocess.check_output(
                ["ffprobe","-v","error","-show_entries","format=duration",
                 "-of","default=noprint_wrappers=1:nokey=1", str(mp4)]).decode().strip())
        except Exception:
            dur = 20
        alt = extract_alt_thumb(mp4, dur * 0.6)
        if not alt:
            _log(f"  {vid} ({v}v) — couldn't extract alt frame, skip"); continue
        if swap_yt_thumbnail(yt, vid, alt):
            _log(f"   {vid} ({v}v, {age_h:.1f}h) — swapped thumbnail to t={dur*0.6:.1f}s frame  '{title[:40]}'")
            seen.add(vid)
            swapped += 1
    _save_seen(seen)
    _log(f"=== summary: swapped={swapped}/{len(cands)} candidates ===")


if __name__ == "__main__":
    run()
