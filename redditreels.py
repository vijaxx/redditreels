#!/usr/bin/env python3
"""
redditreels.py — RedditReels pipeline orchestrator (Pipeline #3).

Flow:
  1. fetch_story   → top weekly self-post from curiosity subs via Reddit RSS (dedup'd)
  2. rewrite_story → Claude haiku rewrites as 45s hook-first narration + title
  3. voice_gen     → edge-tts (Andrew Neural) MP3 + per-word timings
  4. render        → 1080x1920 Minecraft parkour bg + ALL-CAPS karaoke pulse caps
  5. upload        → YouTube (own impl, UNLISTED default) + optional FB/Rumble
                     (FB/Rumble OFF by default — they're FW brand accounts, keep clean)

Runs independently of FrameWise and ScrollStop.
Different LaunchAgent label (com.redditreels.pipeline) + offset schedule.

CLI:
  --dry-run    render only, no upload
  --public     ship YT as public (default unlisted)
  --no-youtube / --no-facebook / --no-rumble    selectively skip a platform
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
FRAMEWISE = Path.home() / "RedditReels"  # legacy alias, now self-hosted

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("redditreels")


def load_cfg() -> dict:
    return json.loads((BASE / "config" / "credentials.json").read_text())


# ------------------------- Dedup -------------------------

USED_LOG = LOG_DIR / "used_stories.json"
KEEP_USED = 80  # rolling history


def load_used() -> list:
    if USED_LOG.exists():
        try: return json.loads(USED_LOG.read_text())
        except Exception: return []
    return []


def append_used(url: str):
    used = load_used()
    used.append({"url": url, "ts": datetime.now().isoformat()})
    used = used[-KEEP_USED:]
    USED_LOG.write_text(json.dumps(used, indent=2))


def is_used(url: str) -> bool:
    used_urls = {u["url"] for u in load_used()}
    return url in used_urls


# ------------------------- Pipeline steps as subprocesses -------------------------

SKIP_RC = 7  # rewrite_story uses this to signal "story too explicit — fetch another"


def _embed_mp4_metadata(mp4_path: Path, title: str, hook: str, narration: str, sub: str) -> None:
    """Embed title/comment/keywords directly into mp4 metadata. YT's content-id
    indexer reads these for additional SEO signal beyond title/description."""
    import subprocess as _sp
    # 2026-06-24: dropped platform-foreign / spam keywords (shorts, viral, fyp, ai) —
    # FB's Professional Dashboard flags irrelevant tags as limiting distribution, and
    # "ai" in metadata is needless AI-content signal. Keep only faithful, relevant terms.
    keywords = f"reddit storytime {sub} reddit stories"
    tmp = mp4_path.with_suffix(".meta.mp4")
    _sp.run([
        "ffmpeg","-y","-loglevel","error","-i", str(mp4_path),
        "-metadata", f"title={title[:80]}",
        "-metadata", f"comment={hook[:100]}",
        "-metadata", f"description={narration[:300]}",
        "-metadata", f"keywords={keywords}",
        "-metadata", "artist=FrameWise Cinema",
        "-metadata", f"album=Reddit Storytime r/{sub}",
        "-c","copy", str(tmp)
    ], check=True, capture_output=True)
    tmp.replace(mp4_path)


def _sanity_check_reel(reel: Path, work: Path, script: dict, log) -> dict:
    """Verify the rendered reel BEFORE uploading. Catches:
      - TTS mid-stream truncation (captured words << expected)
      - Audio/video duration mismatch (silent or weirdly-short reels)
      - Empty/tiny output files
    Returns {ok: bool, reason: str, ...metrics}. Abort upload if !ok."""
    import subprocess as _sp, json as _json
    out = {"ok": False, "reason": "unknown"}

    if not reel.exists() or reel.stat().st_size < 100_000:
        out["reason"] = f"reel file too small: {reel.stat().st_size if reel.exists() else 0}B"
        return out
    out["size_mb"] = reel.stat().st_size / 1024 / 1024

    try:
        probe = _sp.check_output(["ffprobe","-v","error","-show_entries",
            "stream=codec_type,duration","-of","json", str(reel)]).decode()
        streams = _json.loads(probe).get("streams", [])
        video_dur = next((float(s.get("duration", 0)) for s in streams if s.get("codec_type") == "video"), 0.0)
        audio_dur = next((float(s.get("duration", 0)) for s in streams if s.get("codec_type") == "audio"), 0.0)
        out["video_dur"] = video_dur
        out["audio_dur"] = audio_dur
    except Exception as e:
        out["reason"] = f"ffprobe failed: {e}"
        return out

    # Video+audio must both be present + roughly same length.
    # 2026-06-07: floor raised 5→20s — a real RedditReels story is ~40s; anything
    # under 20s is a stub (short/junk source) that the word-gate should've caught.
    # This is the last-resort backstop before upload.
    if video_dur < 20 or audio_dur < 20:
        out["reason"] = f"too short (<20s): video={video_dur:.1f}s audio={audio_dur:.1f}s"
        return out
    if abs(video_dur - audio_dur) > 2.0:
        out["reason"] = f"video/audio mismatch: video={video_dur:.1f}s audio={audio_dur:.1f}s"
        return out

    # Check TTS captured enough of the script
    timings_path = work / "timings.json"
    expected_words = len(script.get("narration", "").split())
    captured_words = 0
    if timings_path.exists():
        try:
            captured_words = len(_json.loads(timings_path.read_text()))
        except Exception:
            pass
    out["expected_words"] = expected_words
    out["captured_words"] = captured_words
    out["word_ratio"] = captured_words / max(1, expected_words)

    if expected_words > 0 and out["word_ratio"] < 0.80:
        out["reason"] = f"TTS truncation: only {captured_words}/{expected_words} words ({out['word_ratio']:.0%})"
        return out

    # Audio duration should be > 60% of script-expected (~2.5 words/sec → expected_words/2.5 sec)
    expected_audio_min = expected_words / 5.0  # generous: up to 5 words/sec (edge-tts +8% can exceed 4)
    if expected_words > 30 and audio_dur < expected_audio_min:
        out["reason"] = f"audio too short for script: {audio_dur:.1f}s < {expected_audio_min:.1f}s (script has {expected_words} words)"
        return out

    # AUDIO ENERGY CHECK — catches silent-but-correct-duration audio (the afade bug from 2026-05-30)
    # Mean volume should be > -50 dB for a real voice track. Silent audio reports -91 dB or -inf.
    try:
        vol_out = _sp.run(["ffmpeg","-i", str(reel), "-af","volumedetect","-f","null","-"],
                          capture_output=True, text=True, timeout=30)
        import re as _re
        m = _re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", vol_out.stderr or "")
        if m:
            mean_db = float(m.group(1))
            out["audio_mean_db"] = mean_db
            if mean_db < -50:
                out["reason"] = f"audio too quiet (mean {mean_db:.1f}dB) — likely silent/broken voice track"
                return out
    except Exception as e:
        out["audio_energy_check_err"] = str(e)

    out["ok"] = True
    out["reason"] = "all checks passed"
    return out


def run_step(name: str, script: str, env: dict):
    log.info(f"=== {name} ===")
    proc = subprocess.run(
        ["/usr/bin/python3", str(BASE / "pipeline" / script)],
        env={**os.environ, **env},
        capture_output=True, text=True,
    )
    if proc.stdout: log.info(proc.stdout.strip())
    if proc.returncode == SKIP_RC:
        return SKIP_RC
    if proc.returncode != 0:
        log.error(f"{script} FAILED: {proc.stderr.strip()}")
        raise RuntimeError(f"step {name} failed (rc={proc.returncode})")
    return 0


def fetch_story_with_dedup(env: dict, work: Path, max_retries: int = 6) -> dict:
    """Run fetch_story.py up to N times until we get a non-duplicate story.
    BUG FIX 2026-05-31: honor RR_FORCE_STORY_URL env var for the rerender-losers
    flow. Previously this was ignored so 'rerender the same story with alt hook'
    silently picked a brand-new story instead, defeating the whole point."""
    forced_url = os.environ.get("RR_FORCE_STORY_URL")
    if forced_url:
        log.info(f"RR_FORCE_STORY_URL set → forcing story {forced_url}")
        # Pass the URL to fetch_story.py so it pulls THIS specific story instead of trending
        forced_env = dict(env)
        forced_env["FORCE_STORY_URL"] = forced_url
        run_step("fetch_story (forced)", "fetch_story.py", forced_env)
        story = json.loads((work / "story.json").read_text())
        if story.get("url") and forced_url.rstrip("/") in story["url"].rstrip("/"):
            log.info(f"forced story loaded: {story['url']}")
            return story
        log.warning(f"forced fetch returned different URL ({story.get('url')}) — fetch_story.py may not support FORCE_STORY_URL yet; falling back to trending pick")
        # fall through to normal dedup loop

    for attempt in range(1, max_retries + 1):
        run_step(f"fetch_story (try {attempt})", "fetch_story.py", env)
        story = json.loads((work / "story.json").read_text())
        if not is_used(story["url"]):
            log.info(f"new story selected: {story['url']}")
            return story
        log.info(f"already used → retrying ({story['url']})")
    log.warning("dedup exhausted, using last fetched story anyway")
    return json.loads((work / "story.json").read_text())


# ------------------------- YouTube upload (own impl) -------------------------

def upload_youtube(video: Path, title: str, description: str, tags: list,
                   cfg: dict, privacy: str, thumbnail: Path | None = None) -> str:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=cfg["youtube_refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"],
        client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "24",  # Entertainment — fits Reddit storytelling
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    # 2026-07-01: chunksize=-1 caused [Errno 32] Broken Pipe on a 28MB upload (16-min hang
    # then dead). Fixed: 5MB chunks + 3 resume-retries so a dropped connection restarts
    # from the last committed chunk, not from byte 0.
    CHUNK = 5 * 1024 * 1024  # 5 MB — small enough to resume quickly after a drop
    media = MediaFileUpload(str(video), chunksize=CHUNK, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media,
                              notifySubscribers=True)
    resp = None
    _yt_retries = 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
        except Exception as _chunk_err:
            _yt_retries += 1
            if _yt_retries > 3:
                raise
            import time as _t
            log.warning(f"   YT chunk error (retry {_yt_retries}/3): {_chunk_err} — resuming in 15s")
            _t.sleep(15)
            continue
        if status:
            log.info(f"   YT upload {int(status.progress()*100)}%")
    vid = resp["id"]

    # Custom thumbnail. NOTE: this requires (a) `youtube` scope (not just upload-only)
    # AND (b) the YT channel to be VERIFIED (phone-verified in YT Studio). If the
    # channel isn't verified, YT returns 403 "user doesn't have permissions to upload
    # and set custom video thumbnails" — a channel-level limit, not a scope issue.

    # Build broad-scope client for thumbnail/captions if available
    yt_broad = yt
    if cfg.get("youtube_refresh_token_broad"):
        try:
            broad_creds = Credentials(
                token=None, refresh_token=cfg["youtube_refresh_token_broad"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
                scopes=["https://www.googleapis.com/auth/youtube",
                        "https://www.googleapis.com/auth/youtube.force-ssl"],
            )
            broad_creds.refresh(Request())
            yt_broad = build("youtube", "v3", credentials=broad_creds)
        except Exception:
            pass

    if thumbnail and thumbnail.exists():
        try:
            from googleapiclient.http import MediaFileUpload as _MFU
            yt_broad.thumbnails().set(
                videoId=vid,
                media_body=_MFU(str(thumbnail), mimetype="image/png", resumable=False),
            ).execute()
            log.info(f"   thumbnail set: {thumbnail.name}")
        except Exception as e:
            msg = str(e)
            if "doesn't have permissions" in msg or "forbidden" in msg.lower():
                log.info(f"   thumbnail SKIP — channel not verified for custom thumbs (verify in YT Studio → Settings → Channel → Feature eligibility). YT auto-thumb will be used.")
            else:
                log.warning(f"   thumbnail upload failed: {e}")
    return vid


# ------------------------- FB / Rumble (decoupled copies) -------------------------

def _ensure_chrome():
    script = FRAMEWISE / "ensure_chrome.sh"
    if script.exists():
        subprocess.run([str(script)], check=False, capture_output=True, timeout=40)


def _load_decoupled(modname: str, file: Path):
    spec = importlib.util.spec_from_file_location(modname, str(file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def upload_rumble(video: Path, title: str, description: str, tags: list, cfg: dict) -> str:
    mod = _load_decoupled("redditreels_rumble", BASE / "platforms" / "rumble_chrome.py")
    _ensure_chrome()
    return mod.upload(video, title=title.replace(" #Shorts", "").strip()[:80],
                      description=description, tags=tags, cfg=cfg)


def upload_facebook(video: Path, title: str, description: str, tags: list, cfg: dict) -> str:
    # 2026-06-30: prefer the official Graph API (robust). The browser path
    # (facebook_chrome.py) is 0/52 — FB changed the Reels composer UI so it can never find
    # the Publish button, AND it risks the once-flagged account. So:
    #   * if a Page access token is configured → post via Graph API (facebook_api.py)
    #   * else → skip FB cleanly (browser fallback is OFF unless explicitly re-enabled)
    fb_api = _load_decoupled("redditreels_fb_api", BASE / "platforms" / "facebook_api.py")
    if fb_api.can_post(cfg):
        return fb_api.upload_reel_api(video, description, cfg, _log=log)
    if cfg.get("facebook_allow_browser_fallback", False):
        mod = _load_decoupled("redditreels_facebook", BASE / "platforms" / "facebook_chrome.py")
        _ensure_chrome()
        return mod.upload(video, title=title, description=description, tags=tags, cfg=cfg)
    raise RuntimeError("FB_NO_TOKEN: set facebook_page_access_token in credentials.json "
                       "(see platforms/FB_API_SETUP.md). Browser fallback off (0/52 + flag risk).")


# ------------------------- Title / description / tags -------------------------

# Words that, if present in the final narration/title/desc, strongly predict YT
# "limited or no ads" (yellow $). We can't query the real monetization status
# without a broader OAuth scope (see tools/check_monetization.py), so this is
# the at-upload heuristic. The ad-safe scrubber in rewrite_story should keep
# these out — this is the canary that confirms it did.
AD_RISK_WORDS = {
    # explicit sexual
    "sex", "sexy", "sexual", "orgasm", "masturbate", "masturbating", "porn",
    "smut", "smutty", "horny", "naked", "nude",
    # body
    "dick", "cock", "pussy", "boobs", "tits",
    # profanity
    "fuck", "fucking", "fucked", "shit", "shitty", "bitch", "asshole",
    # violence / sensitive (also demonetization triggers)
    "kill", "killed", "killing", "suicide", "murder", "rape", "raped", "abuse",
    "blood", "bleeding", "gun", "shooting", "shot", "weapon",
    # discriminatory
    "nigga", "nigger", "faggot", "retard",
}


def estimate_ad_safety(title: str, description: str, narration: str) -> dict:
    """Heuristic at-upload monetization predictor. Returns:
       { ad_safe: 'green'|'yellow'|'red', risk_words: [...], score: 0-100 }
       - green:  no risk words → likely fully monetized
       - yellow: 1-2 risk words → likely limited ads
       - red:    3+ risk words → likely non-monetized
    """
    import re
    blob = f"{title} {description} {narration}".lower()
    tokens = set(re.findall(r"[a-z']+", blob))
    hits = sorted(tokens & AD_RISK_WORDS)
    if len(hits) == 0:
        tier = "green"; score = 100
    elif len(hits) <= 2:
        tier = "yellow"; score = 60
    else:
        tier = "red"; score = 25
    return {"ad_safe": tier, "risk_words": hits, "score": score}


HASHTAGS_BROAD = ["#shorts", "#reddit", "#redditstories", "#storytime", "#fyp"]
# FB-specific tag policy (2026-06-24). Facebook's Professional Dashboard explicitly
# warns that irrelevant hashtags limit distribution. #shorts (YouTube) and #fyp
# (TikTok) are platform-foreign on Facebook, so the FB caption uses only relevant
# tags. FB best practice is a handful of on-topic tags, NOT a 25-tag dump.
HASHTAGS_FB_CORE = ["#reddit", "#redditstories", "#storytime"]
HASHTAGS_FB_DROP = {"#shorts", "#fyp", "#fy", "#foryou", "#foryoupage",
                    "#viral", "#trending", "#viralvideo"}
HASHTAGS_NICHE_BY_SUB = {
    "tifu":              ["#tifu", "#confession", "#embarrassing"],
    "AmItheAsshole":     ["#aita", "#amitheasshole", "#relationships"],
    "MaliciousCompliance":["#maliciouscompliance", "#revenge", "#workstories"],
    "pettyrevenge":      ["#revenge", "#pettyrevenge", "#satisfying"],
    "confession":        ["#confession", "#secret", "#truestory"],
    "AskReddit":         ["#askreddit", "#crazy", "#truestory"],
}


def get_trending_tags():
    """Pull today's trending hashtags from the daily-refreshed file. Returns [] on failure."""
    try:
        sys.path.insert(0, str(BASE / "tools"))
        from trending_hashtags import load_trending_for_category
        return load_trending_for_category("reddit", k_evergreen=2, k_dynamic=4)
    except Exception as e:
        log.info(f"trending tags unavailable: {e}")
        return []


def extract_nlp_tags(script_text: str, max_tags: int = 8) -> list:
    """Extract proper nouns + key noun phrases from the script for YT tag enrichment.
    No external NLP lib needed — uses simple regex heuristics for capitalized words +
    word-frequency for repeated meaningful nouns."""
    import re as _re
    from collections import Counter
    if not script_text: return []
    # Capitalized words (likely proper nouns): "ACOTAR", "Spotify", "Karen", "Reddit"
    caps = _re.findall(r'\b([A-Z][a-zA-Z]{2,15})\b', script_text)
    # Skip overly-common words / sentence starters
    skip = {"My","His","Her","Their","Our","Your","The","This","That","These","Those",
            "I","You","He","She","We","They","And","But","Or","So","If","Then","When",
            "While","Because","Now","Today","Yesterday","Tomorrow","Just","Only",
            "Like","About","With","From","Into","Onto","Over","Under","After","Before"}
    caps = [c for c in caps if c not in skip and len(c) >= 4]
    cap_freq = Counter(caps)
    # Also look for repeated multi-syllable common nouns (occur 2+ times)
    words = [w.lower() for w in _re.findall(r'\b[a-z]{5,15}\b', script_text)]
    common_skip = {"about","after","before","always","never","through","because","really",
                   "actually","literally","probably","another","everything","something",
                   "anything","without","myself","yourself","himself","herself","ourselves"}
    word_freq = Counter([w for w in words if w not in common_skip])
    # Combine: caps top 4 + repeated common-noun top 4
    tags = [c for c,_ in cap_freq.most_common(4)]
    tags += [w for w,c in word_freq.most_common(8) if c >= 2 and w not in [t.lower() for t in tags]][:4]
    return tags[:max_tags]


def get_winner_promo():
    """Returns a 'watch the one going viral' promo line for the channel's top reel, or ''."""
    try:
        sys.path.insert(0, str(BASE / "tools"))
        from find_winner import find_top_video
        url, views = find_top_video()
        if url:
            return f" The one everyone's watching: {url}"
    except Exception as e:
        log.info(f"winner promo unavailable: {e}")
    return ""


def fb_hashtags(tags_hash: list, niche: list) -> list:
    """FB-relevant hashtag line: core reddit tags + the sub's niche tag(s) + up to 2
    real per-video topic tags. Drops #shorts/#fyp and other platform-foreign / spammy
    tags FB flags as irrelevant distribution-limiters (see HASHTAGS_FB_DROP)."""
    out = list(HASHTAGS_FB_CORE)
    seen = {t.lower() for t in out}
    for t in (niche or []):
        if t.startswith("#") and t.lower() not in seen:
            out.append(t); seen.add(t.lower())
    drop = {d.lower() for d in HASHTAGS_FB_DROP}
    extra = 0
    for t in tags_hash:  # mined/niche/nlp/trending, Claude-ordered most-relevant first
        tl = t.lower()
        if not t.startswith("#") or tl in seen or tl in drop:
            continue
        out.append(t); seen.add(tl); extra += 1
        if extra >= 2:
            break
    return out[:6]


def product_promo_line(cfg: dict) -> str:
    """Soft cross-promo pointer to the Gumroad product catalog -- turns views into
    product traffic, since most of these platforms don't otherwise monetize well
    on their own. Appears in every platform's description (YT/Rumble/FB).

    Defaults to the live store URL so this works out of the box without a creds
    edit; a creds `product_catalog_url` overrides it, and `product_promo_enabled:
    false` turns the line off entirely. Copy stays truthful to what the store
    actually is (a puzzle press) rather than implying it sells something else."""
    if not cfg.get("product_promo_enabled", True):
        return ""
    url = (cfg.get("product_catalog_url") or "https://evergreenpuzzlepress.gumroad.com").strip()
    if not url:
        return ""
    return f" More from Evergreen Puzzle Press → {url}"


def build_description(script: dict, story: dict, cfg: dict,
                      bg_credit: str | None = None) -> tuple[str, list, str]:
    sub = story["subreddit"]
    niche = HASHTAGS_NICHE_BY_SUB.get(sub, ["#truestory"])
    trending = get_trending_tags()  # fresh tags per upload — algo loves freshness
    # NLP-extracted tags from the actual script content (proper nouns + repeated terms)
    nlp_tags = ["#" + t.lower().replace(" ","") for t in extract_nlp_tags(script.get("narration",""))]
    # Per-video Claude-mined hashtags: 25 discoverability-optimized tags based on the
    # actual title, narration, and theme. Falls back to static lists if unavailable.
    mined_tags = []
    try:
        import sys as _s
        _s.path.insert(0, str(Path.home() / "RedditReels/tools"))
        from hashtag_miner import mine_hashtags
        mined_tags = mine_hashtags(
            title=script.get("title", ""),
            narration=script.get("narration", ""),
            subreddit=sub, theme=script.get("title_pattern", "story"),
            anthropic_api_key=cfg.get("anthropic_api_key", "")
        )
    except Exception as _e:
        log.warning(f"hashtag_miner unavailable, using static lists: {_e}")
    # Order: mined (Claude-optimized for THIS video) → niche → broad → NLP → trending
    tags_hash = list(dict.fromkeys(mined_tags + niche + HASHTAGS_BROAD + nlp_tags + trending))[:25]
    plain_tags = [t.lstrip("#") for t in tags_hash]
    fb_tags = fb_hashtags(tags_hash, niche)  # short, FB-relevant set (no #shorts/#fyp)
    bg = bg_credit or cfg.get("redditreels", {}).get("gameplay_credit", "")
    winner_promo = get_winner_promo()  # cross-post bump to channel's top performer
    # SEO-dense first 100 chars: hook + sub + key terms (YT search reads first 100 chars heavily)
    seo_first_line = f"{script['hook']} | r/{sub} | reddit stories storytime"
    # Amazon Associates affiliate links -- empty string if not configured
    affiliate = ""
    try:
        import sys as _s
        _s.path.insert(0, str(Path.home() / "RedditReels/tools"))
        from affiliate_links import affiliate_block
        affiliate = affiliate_block(sub, script.get("title_pattern", ""))
    except Exception: pass
    product_promo = product_promo_line(cfg)  # soft Gumroad/Amazon pointer (no-op if unset)
    # "Am I The Villain?" series sign-off: an engagement-bait CTA that ties back to the
    # recurring spoken verdict hook ("you decide — am I the villain?"). Config-driven,
    # defaults to the live value so it works without a creds edit.
    series_signoff = (cfg.get("series_signoff", " Verdict in the comments — you judge.")
                      if cfg.get("series_enabled", True) else "")
    # Shared human-readable body (everything except the platform-specific hashtag line).
    body_lines = [
        seo_first_line,
        "",
        script["hook"],
        series_signoff,
        "",
        f" Original story: r/{sub} — {story.get('url','')}",
        bg,
        winner_promo,
        affiliate,        # added 2026-06-03 — empty unless amazon_affiliate_tag set in creds
        product_promo,    # added 2026-06-24 — soft product cross-promo (empty unless product_catalog_url set)
        "",
        " DAILY SCHEDULE — 4 fresh reels/day IST",
        "• 5:30 PM • 6:30 PM • 7:30 PM • 8:30 PM",
        "",
    ]
    # YouTube/Rumble: full 25-tag dump + Claude SEO search/FAQ enrichment.
    base_desc = "\n".join([l for l in body_lines + [" ".join(tags_hash)] if l])
    # 2026-06-03 overnight: enrich with SEO search-phrase + FAQ blocks
    try:
        import sys as _ss
        _ss.path.insert(0, str(Path.home() / "RedditReels/tools"))
        from seo_description import enrich_description
        base_desc = enrich_description(base_desc, script.get("title",""),
                                         script.get("narration",""), sub,
                                         cfg.get("anthropic_api_key", ""))
    except Exception: pass
    # Facebook: same body, but a short FB-relevant hashtag line (drops #shorts/#fyp) and
    # NO YT-search "People also search/FAQ" block (irrelevant on FB, reads as keyword spam).
    fb_desc = "\n".join([l for l in body_lines + [" ".join(fb_tags)] if l])
    return base_desc, plain_tags, fb_desc


# ------------------------- Orchestration -------------------------

def _quality_tag_safe(script: dict) -> dict:
    """Safe wrapper for reel_quality_tagger — returns {} on any error."""
    try:
        import sys as _s
        _s.path.insert(0, str(BASE / "tools"))
        from reel_quality_tagger import tag
        return tag(script.get("narration",""), script.get("title",""), script.get("hook",""))
    except Exception:
        return {}


def run(args):
    cfg = load_cfg()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = BASE / "processing" / ts
    work.mkdir(parents=True, exist_ok=True)
    env = {"RR_ROOT": str(BASE), "RR_WORK": str(work)}
    log.info(f"=== RedditReels run {ts} ===  work={work}")

    # 1+2. Story (dedup) → Rewrite (ad-safe; may skip on too-explicit, retry up to 8×)
    script = None
    for explicit_retry in range(1, 9):
        story = fetch_story_with_dedup(env, work)
        rc = run_step("rewrite_story", "rewrite_story.py", env)
        if rc == SKIP_RC:
            log.info(f"story too explicit to ad-safe rewrite (try {explicit_retry}) → marking used + fetching another")
            append_used(story["url"])  # don't re-pick the same one
            continue
        candidate = json.loads((work / "script.json").read_text())
        # Rejects too-short narrations that would render to 10-15s stub reels (junk from
        # short/regional source posts). The floor is variant-aware: "rapid21" deliberately
        # targets ~24s for Facebook's under-30s preference, so it clears a lower floor (44
        # words, ~22s) that's still well above a stub; "full45" keeps the 70-word floor.
        # Refetches rather than shipping a stub.
        _nwords = len(candidate.get("narration", "").split())
        _floor = 44 if candidate.get("duration_variant") == "rapid21" else 70
        if _nwords < _floor:
            log.warning(f"MIN-LENGTH GATE: narration only {_nwords} words (<{_floor} "
                        f"for {candidate.get('duration_variant')}) (try {explicit_retry}) → "
                        f"too short, marking used + fetching another")
            append_used(story["url"])
            continue
        # HARD AD-SAFE GATE (2026-06-05): rewrite_story's SKIP_RC relies on the LLM's
        # own judgment, which let "rape play" / "trans misogyny" slip through to a
        # PUBLIC monetized channel. Re-check the *rewritten* narration+title with the
        # deterministic heuristic and BLOCK anything not green. Monetization safety
        # (rule #1) > throughput: skip + fetch another rather than risk a strike.
        safety = estimate_ad_safety(candidate.get("title", ""), "", candidate.get("narration", ""))
        if safety["ad_safe"] != "green":
            log.warning(f"AD-SAFE GATE blocked {safety['ad_safe'].upper()} story "
                        f"(score={safety['score']} risk={safety['risk_words']}) "
                        f"(try {explicit_retry}) → marking used + fetching another")
            append_used(story["url"])
            continue
        script = candidate
        break
    if script is None:
        log.error("8 stories in a row failed the ad-safe gate — aborting run")
        return 1

    # 3. TTS
    run_step("voice_gen", "voice_gen.py", env)

    # 4. Render
    run_step("render", "render.py", env)
    reel = work / "reel.mp4"
    if not reel.exists():
        log.error(f"render produced no output at {reel}")
        return 1

    # Copy to reels/ with a clean filename for archival
    final = BASE / "reels" / f"{ts}_{story['subreddit']}.mp4"
    final.parent.mkdir(exist_ok=True)
    shutil.copy2(reel, final)
    log.info(f"final reel: {final}  ({final.stat().st_size/1024/1024:.1f} MB)")

    # 4-pre. EMBED METADATA IN MP4 (YT indexes mp4 metadata tags for search)
    try:
        _embed_mp4_metadata(reel, script.get("title",""), script.get("hook",""),
                            script.get("narration",""), story.get("subreddit",""))
    except Exception as e:
        log.warning(f"mp4 metadata embed failed (non-blocking): {e}")

    # 4a. SANITY CHECK — verify TTS + render didn't produce a broken reel
    # (catches edge-tts mid-stream truncation that bypasses voice_gen's own verification,
    # plus video/audio duration mismatches that would result in silent or super-short reels)
    try:
        sanity = _sanity_check_reel(reel, work, script, log)
        if not sanity["ok"]:
            log.error(f"PRE-UPLOAD SANITY FAILED → aborting upload: {sanity['reason']}")
            log.error(f"  details: {sanity}")
            append_used(story["url"])  # so we don't pick same story next time
            return 2
        log.info(f"sanity check OK: video {sanity['video_dur']:.1f}s  audio {sanity['audio_dur']:.1f}s  "
                 f"words {sanity['captured_words']}/{sanity['expected_words']} ({sanity['word_ratio']:.0%})")
    except Exception as e:
        log.warning(f"sanity check error (non-blocking): {e}")

    # 4a-bis. DEEPER PRE-UPLOAD SANITY (added 2026-06-03 overnight)
    # Catches silent uploads + audio-too-quiet + landscape misorientations BEFORE shipping
    try:
        import sys as _s
        _s.path.insert(0, str(BASE / "tools"))
        from preupload_sanity import check as _deep_sanity
        passed, fails = _deep_sanity(reel)
        if not passed:
            log.error(f"DEEP SANITY FAILED → aborting upload:")
            for f in fails: log.error(f"  - {f}")
            append_used(story["url"])
            return 2
        log.info("deep sanity OK (audio present, level OK, portrait orientation)")
    except Exception as e:
        log.warning(f"deep sanity error (non-blocking): {e}")

    # 4b. Custom thumbnail (high-CTR override for YT auto-thumbnail)
    thumb_path = work / "thumb.png"
    try:
        sys.path.insert(0, str(BASE / "pipeline"))
        import thumbnail as _tb
        _tb.build_thumbnail(final, script["hook"], thumb_path)
        log.info(f"thumbnail: {thumb_path}")
    except Exception as e:
        log.warning(f"thumbnail generation failed: {e}")
        thumb_path = None

    if args.dry_run:
        log.info("--dry-run set, skipping uploads")
        append_used(story["url"])
        return 0

    # 5. Uploads
    privacy = "public" if args.public else cfg.get("youtube_privacy_default", "unlisted")
    enabled = cfg.get("enabled_platforms", ["youtube"])
    bg_credit_file = work / "bg_credit.txt"
    bg_credit = bg_credit_file.read_text().strip() if bg_credit_file.exists() else None
    description, tags, fb_description = build_description(script, story, cfg, bg_credit=bg_credit)
    title = script["title"][:95]
    results = {}

    # POST-DESCRIPTION AD-SAFE CHECK: the pre-rewrite gate (above) only saw the narration/title
    # with empty description. SEO enrichment and hashtag tools run inside build_description()
    # and can inject risk words (e.g. "sexual" from a FAQ block). Re-check the full text now
    # before any upload happens — yellow/red descriptions waste a Rumble slot and flag the channel.
    _post_desc_safety = estimate_ad_safety(title, description, script.get("narration", ""))
    if _post_desc_safety["ad_safe"] != "green":
        log.warning(f"POST-DESC AD-SAFE GATE blocked {_post_desc_safety['ad_safe'].upper()} "
                    f"(score={_post_desc_safety['score']} risk={_post_desc_safety['risk_words']}) "
                    f"— description enrichment added risk words after the rewrite gate. "
                    f"Stripping risk words from description and continuing.")
        import re as _re_safe
        _risk_pattern = _re_safe.compile(
            r'\b(' + '|'.join(_re_safe.escape(w) for w in _post_desc_safety["risk_words"]) + r')\b',
            _re_safe.IGNORECASE)
        description    = _risk_pattern.sub("[removed]", description)
        fb_description = _risk_pattern.sub("[removed]", fb_description)
        # Re-check: if still not green after scrub, abort (3+ risk words = red, can't scrub safely)
        _final_safety = estimate_ad_safety(title, description, script.get("narration", ""))
        if _final_safety["ad_safe"] != "green":
            log.error(f"Description still {_final_safety['ad_safe'].upper()} after scrub — aborting upload for this story")
            append_used(story["url"])
            return 2

    if "youtube" in enabled and not args.no_youtube:
        try:
            t0 = time.time()
            vid = upload_youtube(final, title, description, tags, cfg, privacy, thumbnail=thumb_path)
            results["youtube"] = f"https://youtube.com/shorts/{vid}"
            log.info(f"YouTube ({privacy}) {time.time()-t0:.1f}s: {results['youtube']}")
            # Engagement boost: post pinned comment (skips silently if no broad scope)
            try:
                import sys as _sys
                _sys.path.insert(0, str(BASE / "tools"))
                import yt_engagement as _eng
                tid = _eng.pin_engagement_comment(vid, _eng.RR_PROMPTS, cfg, seed=vid)
                if tid: results["yt_engagement_comment"] = tid
            except Exception as _e:
                log.warning(f"engagement comment failed: {_e}")
            # SEO unlock: upload SRT captions so YT indexes the transcript for search
            try:
                import sys as _sys
                _sys.path.insert(0, str(BASE / "pipeline"))
                from srt_export import write_srt_from_work
                srt_path = write_srt_from_work(work)
                if srt_path and srt_path.exists() and cfg.get("youtube_refresh_token_broad"):
                    from google.oauth2.credentials import Credentials as _C
                    from google.auth.transport.requests import Request as _R
                    from googleapiclient.discovery import build as _B
                    from googleapiclient.http import MediaFileUpload as _MFU
                    _creds = _C(token=None, refresh_token=cfg["youtube_refresh_token_broad"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
                        scopes=["https://www.googleapis.com/auth/youtube",
                                "https://www.googleapis.com/auth/youtube.force-ssl"])
                    _creds.refresh(_R())
                    _yt = _B("youtube", "v3", credentials=_creds)
                    _yt.captions().insert(part="snippet", body={
                        "snippet": {"videoId": vid, "language": "en", "name": "English", "isDraft": False}
                    }, media_body=_MFU(str(srt_path), mimetype="application/octet-stream")).execute()
                    log.info(f"   SRT captions uploaded ({srt_path.stat().st_size}B)")
                    results["captions_uploaded"] = True
                    # UPGRADE (2026-06-03 overnight): add Hindi + Spanish translations
                    # for multilingual reach (massive India + LatAm market unlock)
                    try:
                        import sys as _ss
                        _ss.path.insert(0, str(BASE / "tools"))
                        from multilingual_captions import add_translations
                        ml = add_translations(vid, srt_path, log=log)
                        results["multilingual_captions"] = ml
                    except Exception as _mle:
                        log.warning(f"multilingual captions failed (non-blocking): {_mle}")
            except Exception as _e:
                log.warning(f"SRT caption upload failed (non-blocking): {_e}")
        except Exception as e:
            log.error(f"YouTube upload failed: {e}")

    # FACEBOOK (2026-06-15): post on EVERY fire (parity with YT/Rumble) so each of the
    # 4 daily peak-hour fires puts a reel on FB → 4 FB reels/day. Each fire uploads a
    # UNIQUE video, so there is no cross-fire duplicate risk. A per-day ceiling in
    # logs/fb_daily.json caps FB uploads/day. The pre-publish session-death retry lives
    # inside facebook_chrome.py.
    # 2026-06-24: LOWERED 6 → 1. The ceiling had drifted to 6, so ALL 4 daily fires were
    # posting to Facebook (verified 06-23: 4 FB uploads, ~45 min apart in the 14:00-18:00
    # window — no longer "hours apart"). FB's Professional Dashboard is ACTIVELY warning of
    # account restrictions, and this account was already flagged once for "automated behaviour".
    # Strict 1 FB reel/day (7/week) is the safe cadence; grow FB via quality + the <30s lever,
    # NOT volume. Only the FIRST FB-eligible fire each day posts to FB; the rest skip it.
    _FB_DAILY_CEILING = 1  # strict 1 FB reel/day (was 6 → caused 4 FB uploads on 06-23)
    _fb_ok = ("facebook" in enabled) and (not args.no_facebook)
    # 2026-06-30: if FB can't actually post (no API token yet, browser fallback off), skip it
    # cleanly HERE as a single INFO line instead of letting upload_facebook raise a misleading
    # ERROR every fire. The moment facebook_page_access_token is set, FB posts via the API.
    if _fb_ok and not (cfg.get("facebook_page_access_token") or cfg.get("facebook_allow_browser_fallback")):
        _fb_ok = False
        log.info("FB: skipped — no facebook_page_access_token yet (see platforms/FB_API_SETUP.md)")
    _today = datetime.now().strftime("%Y%m%d")
    _fb_state_path = LOG_DIR / "fb_daily.json"
    _fb_succ = 0
    if _fb_ok:
        try:
            _s = json.loads(_fb_state_path.read_text())
            if _s.get("date") == _today:
                _fb_succ = _s.get("successes", 0)
        except Exception:
            pass
        if _fb_succ >= _FB_DAILY_CEILING:
            _fb_ok = False
            log.info(f"FB: skipping — daily ceiling {_FB_DAILY_CEILING} reels already posted today")
    # BEST-OF-DAY GATE (2026-07-05): the single daily FB slot was going to whichever fire
    # happened to run FIRST, not the fire with the proven-best hook. Verified same-day
    # (2026-07-04): the 14:45 fire (pattern B) claimed the slot while the 15:30 and 16:26
    # fires both hit Pattern D ("what happens when [ALLCAPS]", 35.4 avg views vs everything
    # else) — so the slot went to a weaker title while two D-pattern videos landed only on
    # YT/Rumble (~0 views there). This holds the slot for a Pattern-D video when one hasn't
    # posted yet today, falling back to "use it anyway" on the day's last remaining fire so
    # the slot is never left unused. Does NOT change the 1/day ceiling — same FB-ban-safety
    # posture, just spends the one slot on the org's proven-strongest pattern.
    # FAILS OPEN: any error here silently no-ops, preserving today's existing behavior.
    if _fb_ok and _fb_succ == 0:
        try:
            sys.path.insert(0, str(BASE / "tools"))
            from title_ab_test import _is_pattern_d
            if not _is_pattern_d(title):
                import plistlib
                _plist = plistlib.load(open(
                    os.path.expanduser("~/Library/LaunchAgents/com.redditreels.pipeline.plist"), "rb"))
                _now = datetime.now()
                _remaining = [t for t in _plist.get("StartCalendarInterval", [])
                              if (t.get("Hour", 0), t.get("Minute", 0)) > (_now.hour, _now.minute)]
                if _remaining:
                    _fb_ok = False
                    log.info(f"FB: holding today's slot for a Pattern-D fire — "
                             f"{len(_remaining)} more scheduled fire(s) today")
        except Exception as _e:
            log.warning(f"FB best-of-day gate errored (defaulting to normal eligibility): {_e}")
    if _fb_ok:
        url = None
        # 2026-06-20: retry ONCE, but ONLY for FB_PREPUBLISH: errors. Those are raised
        # strictly before the Share click (facebook_chrome.py:391/393/395 — re-attach /
        # composer-open / login-checkpoint), so nothing was posted and a retry cannot
        # duplicate. Post-publish "no new reel" errors (and any other error) break
        # immediately, preserving today's duplicate-safety guarantee. Parity with the
        # Rumble retry below.
        for _fatt in range(2):
            try:
                url = upload_facebook(final, title, fb_description, tags, cfg)
                results["facebook"] = url
                log.info(f"Facebook: {url}")
                break
            except Exception as e:
                msg = str(e)
                log.error(f"Facebook upload failed (attempt {_fatt+1}/2): {e}")
                if _fatt == 0 and msg.startswith("FB_PREPUBLISH:"):
                    time.sleep(8)
                    continue
                break
        if url:
            try:
                _fb_state_path.write_text(json.dumps({"date": _today, "successes": _fb_succ + 1}))
            except Exception as _e:
                log.warning(f"FB: could not write fb_daily.json: {_e}")
        # Engagement-bait pinned comment (parity with YT). Only when a reel was posted.
        # Non-blocking: if it fails, the upload is still considered successful.
        if url:
            try:
                import sys as _sys
                _sys.path.insert(0, str(BASE / "tools"))
                from fb_engagement import post_engagement_bait
                # pass story context so smart_bait generates context-aware reply-bait
                _story_ctx = {
                    "title": script.get("title", ""),
                    "hook": script.get("hook", ""),
                    "narration": script.get("narration", "")
                }
                if post_engagement_bait(url, pin=True, story_ctx=_story_ctx):
                    results["fb_bait_pinned"] = True
                    log.info("   FB engagement-bait comment posted + pinned")
            except Exception as _e:
                log.warning(f"FB engagement-bait failed (non-blocking): {_e}")

    if "rumble" in enabled and not args.no_rumble:
        # 2026-06-09: auto-retry ONCE. Rumble's failures (dropdown/step-2) happen BEFORE
        # the video is published, so a retry cannot create a duplicate. Lifts Rumble's
        # ~82% per-attempt rate much higher. (FB is intentionally NOT auto-retried —
        # its "no new reel" can be a false negative → retry would duplicate.)
        for _ratt in range(2):
            try:
                url = upload_rumble(final, title, description, tags, cfg)
                results["rumble"] = url
                log.info(f"Rumble: {url}")
                break
            except Exception as e:
                log.error(f"Rumble upload failed (attempt {_ratt+1}/2): {e}")
                if _ratt == 0:
                    time.sleep(8)

    append_used(story["url"])

    # At-upload heuristic monetization predictor (real check requires broader
    # OAuth scope — run tools/check_monetization.py after granting it).
    ad_safety = estimate_ad_safety(title, description, script.get("narration", ""))
    log.info(f"ad_safe estimate: {ad_safety['ad_safe']}  score={ad_safety['score']}  "
             f"risk_words={ad_safety['risk_words']}")

    # Persist upload log — one JSON object per line so a daily checker can scan it.
    # BUG FIX 2026-05-31: previously only logged yt_video_id; FB and Rumble URLs
    # were buried in results dict only. rerender_losers + view_scrapers + analytics
    # all needed top-level fb_posted / rumble_url fields — now writing them flat.
    yt_video_id = None
    if results.get("youtube", "").startswith("https://youtube.com/shorts/"):
        yt_video_id = results["youtube"].rsplit("/", 1)[-1]
    fb_posted = results.get("facebook") if results.get("facebook", "").startswith("http") else None
    rumble_url = results.get("rumble") if results.get("rumble", "").startswith("http") else None
    up_log = LOG_DIR / "uploads.jsonl"
    with open(up_log, "a") as f:
        f.write(json.dumps({
            "ts": ts,
            "story_url": story["url"],
            "sub": story["subreddit"],
            "title": title,
            "results": results,
            "yt_video_id": yt_video_id,
            "fb_posted": fb_posted,
            "rumble_url": rumble_url,
            "ad_safe_estimate": ad_safety,
            "ad_safe_scrubbed": script.get("ad_safe_scrubbed", []),
            "duration_variant": script.get("duration_variant"),    # "rapid21" or "full45"
            "target_duration_s": script.get("target_duration_s"),
            "source_type": story.get("source_type", "post"),       # "post" or "comment"
            "title_pattern": script.get("title_pattern"),          # which viral formula (A-E)
            # Log-only semantic title<->story faithfulness verdict from the free Groq judge
            "title_faithful": script.get("title_faithful"),
            "title_faithful_reason": script.get("title_faithful_reason"),
            # Filled in later by tools/check_monetization.py once broader OAuth is granted
            "yt_monetization_real": None,
            "yt_monetization_checked_at": None,
            "quality_tags": _quality_tag_safe(script),
        }) + "\n")

    # Notification on upload completion
    try:
        import sys as _sn
        _sn.path.insert(0, str(BASE / "tools"))
        from notify import notify
        platforms = [p for p in ["youtube","facebook","rumble"] if results.get(p)]
        notify(
            f" RR Posted ({len(platforms)}/3): {title[:55]}",
            f"Story: r/{story.get('subreddit','?')}\n"
            f"Platforms: {', '.join(platforms)}\n"
            f"YT: {results.get('youtube','-')}\n"
        )
    except Exception: pass
    return 0


def main():
    # Single-instance guard: if a previous fire is still uploading to the same Chrome,
    # a new fire will fight it for Rumble/FB selenium control and both will hang.
    # Exit immediately if another instance holds the lock.
    import fcntl
    _lockf = open(BASE / "logs" / ".pipeline.lock", "w")
    try:
        fcntl.flock(_lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("Another pipeline fire is still running — this fire exits to avoid Chrome conflict.")
        sys.exit(0)

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="render only, no uploads")
    p.add_argument("--public", action="store_true", help="YT privacy = public (default unlisted)")
    p.add_argument("--no-youtube", action="store_true")
    p.add_argument("--no-facebook", action="store_true")
    p.add_argument("--no-rumble", action="store_true")
    args = p.parse_args()
    try:
        sys.exit(run(args))
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
