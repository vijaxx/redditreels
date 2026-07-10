#!/usr/bin/env python3
"""
view_scrapers.py — Cross-platform view-count fetcher.

Built 2026-05-31 to fix the broken loser-detection logic that judged videos by
YouTube views alone (which caused yesterday's denture-solution video to be flagged
as a loser at 1 YT view when it had 13 views on FB — would have been worse if it
had 250 views; the principle stands either way). No public FB/Rumble APIs needed.

API:
    get_views_all_platforms(yt_id=None, fb_url=None, rumble_url=None,
                             rumble_title_hint=None) -> dict
        → {"youtube": int|None, "facebook": int|None, "rumble": int|None,
            "total": int (sum of non-None values)}

Scraping strategy per platform:
  YT  — Data API v3 with existing refresh token (no Chrome)
  FB  — Navigate to reel URL, click "View Insights", read "Views" label + number
  RUM — Navigate to /account/content (admin dashboard), text-match title→raw views
"""

import os, re, json, time, sys, pathlib, urllib.request
from typing import Optional, Dict

BASE = pathlib.Path.home()
CFG  = BASE / "RedditReels" / "config" / "credentials.json"
DEBUG_PORT = 9223


# ─────────────────────────────  YOUTUBE  ─────────────────────────────
def get_youtube_views(yt_id: str) -> Optional[int]:
    """Fetch view count from YT Data API v3."""
    if not yt_id:
        return None
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        cfg = json.loads(CFG.read_text())
        rtok = cfg.get("youtube_refresh_token_broad") or cfg.get("youtube_refresh_token")
        creds = Credentials(
            token=None, refresh_token=rtok,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
            scopes=["https://www.googleapis.com/auth/youtube",
                    "https://www.googleapis.com/auth/youtube.force-ssl"],
        )
        yt = build("youtube", "v3", credentials=creds)
        r = yt.videos().list(part="statistics", id=yt_id).execute()
        items = r.get("items", [])
        if not items:
            return 0
        return int(items[0]["statistics"].get("viewCount", 0))
    except Exception as e:
        print(f"  [yt-views] {yt_id} err: {e}", file=sys.stderr)
        return None


# ─────────────────────────────  CHROME ATTACH  ─────────────────────────────
def attach_chrome():
    """Attach to existing Chrome via :9223 (must be running first)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read()
    except Exception as e:
        raise RuntimeError(f"Chrome :{DEBUG_PORT} not reachable. Run ensure_chrome.sh. {e}")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    return webdriver.Chrome(options=opts)


def _parse_count(text: str) -> Optional[int]:
    """'1.2K' / '15K' / '247' / '1,234' → int."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("﻿", "")
    m = re.search(r"([\d.]+)\s*([KMB]?)", text, re.IGNORECASE)
    if not m:
        return None
    n = float(m.group(1))
    suf = m.group(2).upper()
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suf, 1)
    return int(n * mult)


# ─────────────────────────────  FACEBOOK  ─────────────────────────────
def get_facebook_views(fb_url: str, driver=None) -> Optional[int]:
    """Navigate to reel URL, click 'View Insights', extract Views count.
    Works only when logged in as Page admin (which our Chrome :9223 already is)."""
    if not fb_url:
        return None
    own = driver is None
    try:
        if own:
            driver = attach_chrome()
        driver.get(fb_url)
        time.sleep(5)
        # Click 'View Insights' link
        clicked = driver.execute_script("""
        const all = Array.from(document.querySelectorAll('span,a,div[role="button"]'));
        for (const el of all) {
            const t = (el.innerText || '').trim();
            if (/^view insights?$/i.test(t)) {
                let tg = el; let n=0;
                while (tg && n++<5 && tg.getAttribute('role')!=='button' && tg.tagName!=='A')
                    tg = tg.parentElement;
                (tg||el).click();
                return true;
            }
        }
        return false;
        """)
        if not clicked:
            # Not a Page admin? Or interstitial in the way?
            return None
        time.sleep(10)  # insights panel async-loads
        # Parse body text: find line matching 'Views' (with optional BOM) and grab next line
        body = driver.execute_script("return document.body.innerText;")
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            clean = line.strip().replace("﻿", "").lower()
            if clean == "views" or clean == "plays":
                # Take next non-empty line that looks like a number
                for j in range(i + 1, min(i + 4, len(lines))):
                    cand = lines[j].strip()
                    if re.match(r"^[\d.,]+\s*[KMB]?$", cand) and len(cand) < 12:
                        return _parse_count(cand)
                break
        return None
    except Exception as e:
        print(f"  [fb-views] {fb_url} err: {e}", file=sys.stderr)
        return None
    finally:
        if own and driver is not None:
            try: driver.quit()
            except Exception: pass


def get_rumble_views_public(rumble_url):
    """RELIABLE Rumble play count via the PUBLIC video page — no login, no Chrome
    dependency, unlike get_rumble_views() (which needs the admin dashboard, Chrome
    :9223, and a title-fuzzy-match, and returned null on every one of 228 recorded
    videos because that Chrome happened to be down whenever weekly_learn ran).
    Rumble's video page embeds a JSON state blob with the real "rumble_plays" count
    right next to the video's own title (verified 2026-07-05 against 3 real videos —
    title match confirmed exact, not a related-video false-positive). The first
    request 307-redirect-loops behind an anti-bot cookie challenge; a persisted
    cookiejar across the redirect resolves it (no JS execution needed)."""
    if not rumble_url:
        return None
    import re as _re
    import urllib.request as _ur
    import http.cookiejar as _cj
    try:
        jar = _cj.CookieJar()
        opener = _ur.build_opener(_ur.HTTPCookieProcessor(jar))
        opener.addheaders = [("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                              "10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120 Safari/537.36")]
        html = opener.open(rumble_url, timeout=20).read().decode("utf-8", "ignore")
        idx = html.find('"video_stats"')
        if idx == -1:
            return None
        window = html[idx:idx + 500]
        m = _re.search(r'"rumble_plays":(\d+)', window)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def get_facebook_views_graph(fb_url, token):
    """RELIABLE FB reel plays via the Graph API (no DOM scraping). Needs a Page
    access token with read_insights. Returns int plays or None. The reel/video
    id is right in the URL: facebook.com/reel/<ID>."""
    if not fb_url or not token:
        return None
    import urllib.request, urllib.parse, json as _json
    m = (re.search(r"/reel/(\d+)", fb_url) or re.search(r"/videos/(\d+)", fb_url)
         or re.search(r"(\d{6,})", fb_url))
    if not m:
        return None
    vid = m.group(1)
    base = f"https://graph.facebook.com/v21.0/{vid}"
    for metric in ("post_video_views", "blue_reels_play_count",
                   "post_video_views_unique", "post_impressions_unique"):
        try:
            q = urllib.parse.urlencode({"fields": f"video_insights.metric({metric})",
                                        "access_token": token})
            with urllib.request.urlopen(f"{base}?{q}", timeout=20) as r:
                d = _json.loads(r.read())
            data = (d.get("video_insights") or {}).get("data") or []
            if data:
                vals = data[0].get("values") or []
                if vals and vals[0].get("value") is not None:
                    return int(vals[0]["value"])
        except Exception:
            continue
    return None


# ─────────────────────────────  RUMBLE  ─────────────────────────────
# Cache for the Rumble dashboard text — we only need to load it once per run since
# /account/content lists ALL videos with their views.
_RUMBLE_DASHBOARD_CACHE = {"ts": 0, "text": ""}
_RUMBLE_DASHBOARD_TTL = 60  # seconds


def _fetch_rumble_dashboard(driver) -> str:
    """Load all videos in the Rumble admin dashboard (clicks 'Show more' until exhausted).
    BUG FIX 2026-05-31: dashboard initially shows only first 24 — older videos were
    invisible to the scraper until 'Show more' was clicked enough times."""
    now = time.time()
    if now - _RUMBLE_DASHBOARD_CACHE["ts"] < _RUMBLE_DASHBOARD_TTL and _RUMBLE_DASHBOARD_CACHE["text"]:
        return _RUMBLE_DASHBOARD_CACHE["text"]
    driver.get("https://rumble.com/account/content")
    time.sleep(8)
    # Click "Show more" up to 20 times (= ~480 videos covered)
    for _ in range(20):
        clicked = driver.execute_script("""
        const all = Array.from(document.querySelectorAll('a,button,div[role="button"]'));
        for (const el of all) {
            const t = (el.innerText || '').trim().toLowerCase();
            if (/^(show more|load more|more videos)$/i.test(t)) {
                el.scrollIntoView({block:'center'});
                el.click();
                return true;
            }
        }
        return false;
        """)
        if not clicked:
            break
        time.sleep(2)  # let new batch render
    text = driver.execute_script("return document.body.innerText;")
    _RUMBLE_DASHBOARD_CACHE["text"] = text
    _RUMBLE_DASHBOARD_CACHE["ts"] = now
    return text


def get_rumble_views(rumble_url: str, driver=None, title_hint: Optional[str] = None) -> Optional[int]:
    """Match by title in /account/content dashboard, extract 'raw views: N'.
    title_hint is the upload title (from uploads.jsonl). Falls back to extracting
    the slug from rumble_url if title_hint not provided."""
    if not rumble_url and not title_hint:
        return None
    own = driver is None
    try:
        if own:
            driver = attach_chrome()
        dashboard_text = _fetch_rumble_dashboard(driver)
        # Build a lookup key: prefer title_hint, fall back to URL slug
        if title_hint:
            needle = title_hint.strip()
        else:
            m = re.search(r"/v[a-z0-9]+-([a-z0-9-]+)\.html", rumble_url)
            needle = m.group(1).replace("-", " ").strip() if m else None
        if not needle:
            return None
        # NORMALIZE both sides so punctuation differences (apostrophes, em-dashes,
        # smart quotes) don't break the match. "grandad's" must match "grandads",
        # "—" must match "-" or "", etc.
        def _norm(s: str) -> str:
            s = s.lower()
            s = re.sub(r"[''\"\"`]", "", s)   # smart + straight quotes/apostrophes → gone
            s = re.sub(r"[—–-]", " ", s)            # all dashes → space
            s = re.sub(r"[^a-z0-9\s]", " ", s)      # any other punct → space
            s = re.sub(r"\s+", " ", s).strip()
            return s
        norm_needle = _norm(needle)
        title_key = " ".join(norm_needle.split()[:4])
        # Search dashboard line-by-line
        for i, line in enumerate(dashboard_text.split("\n")):
            if title_key and title_key in _norm(line):
                # Look for "raw views: N" in next 30 lines
                lines = dashboard_text.split("\n")
                for j in range(i, min(i + 30, len(lines))):
                    m = re.search(r"raw views?\s*:\s*([\d,]+)", lines[j], re.IGNORECASE)
                    if m:
                        return int(m.group(1).replace(",", ""))
                break
        return None
    except Exception as e:
        print(f"  [rum-views] err: {e}", file=sys.stderr)
        return None
    finally:
        if own and driver is not None:
            try: driver.quit()
            except Exception: pass


# ─────────────────────────────  AGGREGATOR  ─────────────────────────────
def get_views_all_platforms(yt_id: Optional[str] = None,
                             fb_url: Optional[str] = None,
                             rumble_url: Optional[str] = None,
                             rumble_title_hint: Optional[str] = None) -> Dict[str, Optional[int]]:
    """Fetch + sum views across all 3 platforms in one attached-Chrome session."""
    out = {"youtube": None, "facebook": None, "rumble": None, "total": 0}
    if yt_id:
        out["youtube"] = get_youtube_views(yt_id)
    driver = None
    try:
        if fb_url or rumble_url or rumble_title_hint:
            driver = attach_chrome()
        if fb_url:
            out["facebook"] = get_facebook_views(fb_url, driver=driver)
        if rumble_url or rumble_title_hint:
            out["rumble"] = get_rumble_views(rumble_url, driver=driver, title_hint=rumble_title_hint)
    finally:
        if driver is not None:
            try: driver.quit()
            except Exception: pass
    out["total"] = sum(v for v in (out["youtube"], out["facebook"], out["rumble"]) if v is not None)
    return out


# ─────────────────────────────  CLI  ─────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Fetch views across YT+FB+Rumble")
    ap.add_argument("--yt", help="YouTube video ID")
    ap.add_argument("--fb", help="Facebook reel/video URL")
    ap.add_argument("--rum", help="Rumble video URL")
    ap.add_argument("--rum-title", help="Rumble video title (alternative to --rum, more robust)")
    args = ap.parse_args()
    r = get_views_all_platforms(yt_id=args.yt, fb_url=args.fb, rumble_url=args.rum,
                                  rumble_title_hint=args.rum_title)
    print(json.dumps(r, indent=2))
