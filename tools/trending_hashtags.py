#!/usr/bin/env python3
"""
trending_hashtags.py — fetch fresh trending hashtags daily.

Sources:
  1. YouTube search-suggest API (auto-completes for popular terms)
  2. r/popular (Reddit trending — proxy for what people are TALKING about)
  3. Curated "always-hot" Shorts tags (compile from public industry data)

Output: writes ~/.trending_tags.json with the merged top tags per category.
Pipelines read this file when building descriptions.

Run via cron daily (07:05 IST, right after cleanup).
"""
from __future__ import annotations
import datetime, html, json, os, pathlib, re, sys, urllib.parse, urllib.request

OUT = pathlib.Path(os.path.expanduser("~/.trending_tags.json"))

# Always-hot YT Shorts tags by content category — updated as the algo shifts
EVERGREEN = {
    "broad":      ["#shorts", "#viral", "#fyp", "#foryou", "#trending"],
    "shorts":     ["#shortvideo", "#shortsfeed", "#youtubeshorts", "#shortsviral"],
    "reddit":     ["#reddit", "#redditstories", "#redditstorytime", "#storytime", "#truestory"],
    "facts":      ["#facts", "#mindblown", "#didyouknow", "#explained", "#science"],
    "motivation": ["#motivation", "#inspiration", "#mindset", "#discipline", "#growth"],
    "ai":         ["#ai", "#ai2026", "#aishorts", "#aistory", "#artificialintelligence"],
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def fetch_reddit_popular_keywords(n=20):
    """Top-of-r/popular post titles → extract noun keywords. Tags any surging topics.
    BUG FIX 2026-06-01: Reddit started 403'ing the default Safari UA. Try 3 endpoints
    in order with their app-format UA: rss → old.reddit → json. Last endpoint also
    works as backup data source (titles in JSON)."""
    import time as _t, json as _json
    # CRITICAL: Reddit hard-blocks any /top.rss?t=day endpoint with query params (403).
    # The simpler /r/popular.rss (no query) returns 200 reliably with Chrome UA.
    # Discovered 2026-06-01 via curl tests.
    CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    endpoints = [
        ("https://www.reddit.com/r/popular.rss", CHROME_UA, "rss"),
        ("https://old.reddit.com/r/popular.rss", CHROME_UA, "rss"),
        ("https://www.reddit.com/r/popular/top.rss?t=day&limit=25", CHROME_UA, "rss"),  # legacy fallback
        ("https://www.reddit.com/r/popular/top.json?t=day&limit=25", CHROME_UA, "json"),
    ]
    body = None
    fmt = "rss"
    # Reddit checks for full browser-like header set, not just UA. Add Accept-*
    # headers that real browsers send — fixes 403 (verified 2026-06-01: with these
    # headers urllib matches curl behavior).
    common_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",  # don't accept gzip — we're not handling decompression
        "Cache-Control": "no-cache",
    }
    # urllib gets 403 even with full headers (Reddit fingerprints urllib's TLS
    # signature). curl works — shell out to it. Verified working 2026-06-01.
    import subprocess as _sp
    for url, ua, kind in endpoints:
        try:
            r = _sp.run(
                ["curl", "-s", "-f", "--max-time", "15",
                 "-A", ua,
                 "-H", f"Accept: {common_headers['Accept']}",
                 "-H", f"Accept-Language: {common_headers['Accept-Language']}",
                 url],
                capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout and len(r.stdout) > 100:
                body = r.stdout
                fmt = kind
                break
            else:
                print(f"  reddit endpoint {url[:55]}… curl rc={r.returncode} len={len(r.stdout) if r.stdout else 0}", file=sys.stderr)
        except Exception as e:
            print(f"  reddit endpoint {url[:55]}… {e}", file=sys.stderr)
        _t.sleep(2)
    if body is None:
        print(f"  all reddit endpoints failed — skipping reddit_popular this run", file=sys.stderr)
        return []
    if fmt == "json":
        try:
            data = _json.loads(body)
            titles = [c["data"].get("title","") for c in data.get("data",{}).get("children",[])]
        except Exception as e:
            print(f"  json parse failed: {e}", file=sys.stderr); return []
    else:
        titles = [html.unescape(m) for m in re.findall(r"<title>(.*?)</title>", body, re.S)]
    # Pull single-word keywords > 4 chars, not stopwords
    stops = {"the","this","that","with","from","what","when","where","they","their","there","into","about","just","like","very","what","because","while","being","were","more","most","said","gets","didnt","cant","dont","wont","still","also","over","than","then","them","some","into","under","every"}
    words = []
    for t in titles[1:]:  # skip feed header
        for w in re.findall(r"[A-Za-z][A-Za-z']{4,}", t):
            wl = w.lower()
            if wl in stops: continue
            words.append(wl)
    # Count freq
    from collections import Counter
    top = [w for w,_ in Counter(words).most_common(n)]
    return [f"#{w}" for w in top]


def fetch_yt_suggest_keywords(seeds=("reels short", "viral video", "trending today"), n_per=4):
    """YouTube's public suggest endpoint returns popular completions."""
    out = []
    for seed in seeds:
        url = f"https://suggestqueries.google.com/complete/search?client=youtube&q={urllib.parse.quote(seed)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            body = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"\[\s*\"[^\"]+\"\s*,\s*\[(.+?)\]", body)
        if not m: continue
        completions = re.findall(r"\"([^\"]+)\"", m.group(1))
        # Strip the seed prefix from completions, keep distinct words
        for c in completions[:n_per]:
            for w in re.findall(r"[A-Za-z][A-Za-z]{3,}", c):
                if w.lower() not in seed.lower():
                    out.append(f"#{w.lower()}")
    return list(dict.fromkeys(out))[:15]


def main():
    print(f"[trending_hashtags] refreshing {OUT}")
    data = {
        "fetched_at": datetime.datetime.now().isoformat(),
        "evergreen": EVERGREEN,
        "reddit_popular": fetch_reddit_popular_keywords(),
        "yt_suggest": fetch_yt_suggest_keywords(),
    }
    print(f"  reddit_popular: {len(data['reddit_popular'])} tags")
    print(f"  yt_suggest:     {len(data['yt_suggest'])} tags")
    print(f"  evergreen pools: {list(EVERGREEN.keys())}")
    OUT.write_text(json.dumps(data, indent=2))
    print(f"  saved → {OUT}")


def load_trending_for_category(cat: str = "reddit", k_evergreen: int = 5, k_dynamic: int = 5) -> list:
    """Convenience for pipelines: load N evergreen tags for category + N dynamic from reddit/yt suggest."""
    if not OUT.exists():
        return EVERGREEN.get("broad", [])[:k_evergreen]
    try:
        data = json.loads(OUT.read_text())
    except Exception:
        return EVERGREEN.get("broad", [])[:k_evergreen]
    ev = data.get("evergreen", {}).get(cat, []) + data.get("evergreen", {}).get("broad", [])
    dyn = data.get("reddit_popular", []) + data.get("yt_suggest", [])
    import random
    return list(dict.fromkeys(ev[:k_evergreen] + random.sample(dyn, min(k_dynamic, len(dyn)))))


if __name__ == "__main__":
    main()
