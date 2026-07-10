#!/usr/bin/env python3
"""Pull a high-engagement Reddit self-post OR a juicy top-comment via RSS feed."""
import html, json, os, pathlib, random, re, sys, urllib.request, urllib.error

# 2026-06-10 DISABLED (was 0.40): using a random TOP COMMENT as the "story" produced
# garbage — comments aren't narratives (e.g. "whats the big deal, feel his boner later"),
# so the rewriter fabricated incoherent/risky scripts around them. Posts only now.
COMMENT_SOURCE_PROBABILITY = 0.0

# Expanded sub pool — niche subs often have HIGHER engagement per view + less repeat audience
SUBS = [
    "AskReddit", "tifu", "MaliciousCompliance", "AmItheAsshole",
    "pettyrevenge", "confession",
    # NEW high-engagement subs (added 2026-05-30 for source variety)
    "NoStupidQuestions",      # often surprising answers
    "Showerthoughts",         # one-liner gold
    "TwoXChromosomes",        # personal stories
    "ProRevenge",             # more dramatic than pettyrevenge
    "IDontWorkHereLady",      # short funny stories
    "EntitledPeople",         # outrage stories
    "MakeMeSuffer",           # — wait this is image sub, skip
    "antiwork",               # work stories
    "JustNoMIL",              # in-law drama (massive engagement)
    "relationship_advice",    # high-drama personal
]
# Remove image-only subs
SUBS = [s for s in SUBS if s != "MakeMeSuffer"]

# Weights the fetch heavily toward the relationship/family/judgment cluster that already
# dominates the channel's best content, so it reads as one identity instead of random AITA.
#
# A 109-run audit added antiwork, NoStupidQuestions, and MaliciousCompliance to the pool --
# all three came back 100% YT/Rumble success and 100% green ad-safety, the cleanest
# performers around, with zero risk-word hits across 9 combined runs. They fit the
# "judge the situation" format and generate the same kind of conflict/judgment stories, so
# they're now guaranteed participants alongside the core relationship cluster rather than a
# 34%-chance wildcard.
SERIES_SUBS = [
    # Relationship / moral-judgment core (channel identity)
    "AmItheAsshole", "JustNoMIL", "relationship_advice",
    "EntitledPeople", "confession", "TwoXChromosomes",
    # Clean 100% performers — workplace/curiosity judgment stories (added 2026-07-01)
    "antiwork", "NoStupidQuestions", "MaliciousCompliance",
]

def series_active_subs(stable, k=6, off_brand_p=0.15):
    """Return up to k subs heavily weighted to SERIES_SUBS, with a small chance of ONE
    off-brand sub for variety. Falls back to plain sampling if no series sub is available
    (e.g. all blacklisted).
    off_brand_p sits at 0.15 rather than higher -- series subs are all high performers,
    and off-brand adds variety but no proven benefit."""
    series = [s for s in SERIES_SUBS if s in stable]
    other  = [s for s in stable if s not in SERIES_SUBS]
    if not series:
        return random.sample(stable, min(k, len(stable)))
    picks = random.sample(series, min(k, len(series)))
    if other and len(picks) >= 2 and random.random() < off_brand_p:
        picks[-1] = random.choice(other)   # one off-brand sub so the channel isn't 100% monotone
    return picks

# A separate "viral now" sub pool: when a fire hits one of these, use the 'hour' time
# filter so content is under 60 minutes old -- riding the wave while it's still hot.
# About 30% of fires draw from this pool.
TRENDING_SUBS = [
    # 2026-06-07 FIX: removed "popular" + "all" — they surfaced regional/non-English/
    # image/meme/tiny posts (e.g. r/PataHaiAajKyaHua, 193-char junk) that rewrote to
    # 26-word, 11-second reels. Keep only English, substantive, story-friendly subs.
    "news",             # breaking news
    "worldnews",        # global events
    "outoftheloop",     # explains current viral moments
    "todayilearned",    # surprising facts going viral
]
import random as _r_trend
USE_TRENDING_POOL = False  # 2026-06-13: DISABLED — news/worldnews subs yield 0 usable posts
# (link posts, no selftext) and just burn requests → contributed to Reddit burst-429s. Stable subs only.
import os, pathlib
ROOT = pathlib.Path(os.environ.get('RR_ROOT', os.path.expanduser('~/RedditReels')))
WORK = pathlib.Path(os.environ.get('RR_WORK', str(ROOT / 'output')))
WORK.mkdir(parents=True, exist_ok=True)

OUT = pathlib.Path((WORK / "story.json"))
OUT.parent.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (compatible; RedditReels/1.0; +https://example.com)"

def fetch_rss(sub, t=None, limit=20):
    """Fetch top posts. `t` default rotates: 75% 'day' (fresh), 20% 'week' (depth), 5% 'hour' (real-time pulse).
    Heavier weight on fresh content = less audience overlap with what's already gone viral."""
    if t is None:
        r = random.random()
        t = "hour" if r < 0.05 else ("day" if r < 0.80 else "week")
    url = f"https://www.reddit.com/r/{sub}/top.rss?t={t}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # 2026-06-09 FIX: catch ALL exceptions, not just HTTPError/URLError. A bare
    # network blip on ONE sub (timeout, RemoteDisconnected, ConnectionReset,
    # IncompleteRead, socket error) was bubbling up and CRASHING the whole fire
    # (killed the 17:30 fire today). Now a flaky sub just returns None → skipped.
    try:
        return urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
    except Exception as e:
        print(f"  ! {sub}: {e}", file=sys.stderr)
        return None

def parse_entries(rss_body, sub):
    entries = []
    for raw in rss_body.split("<entry>")[1:]:
        title_m = re.search(r"<title>(.*?)</title>", raw, re.S)
        link_m  = re.search(r'<link href="([^"]+)"', raw)
        content_m = re.search(r'<content type="html">(.*?)</content>', raw, re.S)
        author_m  = re.search(r"<name>(.*?)</name>", raw, re.S)
        if not (title_m and content_m): continue
        title = html.unescape(title_m.group(1)).strip()
        content = html.unescape(content_m.group(1))
        # Strip HTML
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        # Reddit RSS prepends a "submitted by ..." footer; remove the trailing "[link] [comments]"
        text = re.sub(r"\[link\]\s*\[comments\]\s*$", "", text).strip()
        entries.append({
            "subreddit": sub,
            "title": title,
            "selftext": text,
            "url": link_m.group(1) if link_m else "",
            "author": (html.unescape(author_m.group(1)) if author_m else "[unknown]"),
        })
    return entries

def is_good(p):
    t = p["selftext"]
    # 2026-06-07 FIX: raised min 400→700. A 400-char post rewrites to ~25-35 words
    # (~12s reel) — too short to retain. 700+ chars gives enough for a real ~40s story.
    if len(t) < 700 or len(t) > 5000: return False
    # Avoid clearly link/image-only posts that RSS still returns
    if "submitted by" in t and len(t) < 250: return False
    # Light profanity guard (the FW/SS pipelines have stronger ones; this is just a sieve)
    bad = ["nsfw", "porn", " sex "]
    low = t.lower()
    if any(b in low for b in bad): return False
    return True

# ---- Top-comments-as-source: pull thread comments via the post's .rss endpoint ----

def fetch_top_comments(post_url: str, min_chars: int = 80, max_chars: int = 600, top_n: int = 15) -> list:
    """Fetch top comments from a post URL via RSS. Returns list of dicts."""
    # Strip trailing slash and append .rss
    base = post_url.rstrip("/")
    rss_url = base + ".rss"
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": UA})
        body = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  comment fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for raw in body.split("<entry>")[1:]:  # entry[0] is the post itself, rest are comments
        content_m = re.search(r'<content type="html">(.*?)</content>', raw, re.S)
        author_m  = re.search(r'<name>(.*?)</name>', raw, re.S)
        link_m    = re.search(r'<link href="([^"]+)"', raw)
        if not content_m: continue
        text = re.sub(r"<[^>]+>", " ", html.unescape(content_m.group(1)))
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\[link\]\s*\[comments\]\s*$", "", text).strip()
        if not (min_chars <= len(text) <= max_chars): continue
        out.append({
            "text": text,
            "author": html.unescape(author_m.group(1)) if author_m else "[unknown]",
            "url": link_m.group(1) if link_m else "",
        })
        if len(out) >= top_n: break
    return out


def score_comment(c: dict) -> float:
    """Heuristic score: prefer short-punchy, with strong hook signals."""
    t = c["text"].lower()
    score = 0.0
    # Length sweet spot: 100-300 chars
    score += max(0, 5 - abs(200 - len(c["text"])) / 50.0)
    # Hook signals
    HOOK_WORDS = {"i", "my", "she", "he", "they", "wait", "actually", "honestly",
                  "imagine", "imagine if", "as someone who", "story time", "this happened"}
    for w in HOOK_WORDS:
        if t.startswith(w + " "): score += 2; break
    # Punchy ending (em-dash, ellipsis, exclamation)
    if t.rstrip()[-1:] in "!?.…": score += 0.5
    if "—" in t or "..." in t: score += 0.5
    # Penalize lowest-effort spam
    if len(set(t.split())) / max(1, len(t.split())) < 0.4: score -= 5
    return score


def fetch_forced_story(force_url: str) -> dict:
    """Fetch a specific Reddit story by URL (for rerender_losers replay).
    Strips the trailing slash + uses Reddit's .json endpoint for clean data."""
    import urllib.request, json as _json
    url = force_url.rstrip("/")
    if not url.endswith(".json"):
        url += ".json"
    req = urllib.request.Request(url, headers={"User-Agent": "RedditReels/1.0 (rerender-replay)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = _json.loads(r.read())
    # data[0] is the post listing; .data.children[0].data is the post
    post = data[0]["data"]["children"][0]["data"]
    return {
        "subreddit": post.get("subreddit", "unknown"),
        "title": post.get("title", ""),
        "selftext": post.get("selftext", "") or post.get("title", ""),
        "url": force_url,
        "author": post.get("author", "unknown"),
        "source_type": "post",
    }


def main():
    # BUG FIX 2026-05-31: honor FORCE_STORY_URL env var (rerender_losers replay).
    # Previously this env var was silently ignored.
    force = os.environ.get("FORCE_STORY_URL")
    if force:
        try:
            pick = fetch_forced_story(force)
            json.dump(pick, open(OUT, "w"), indent=2)
            print(f"[fetch_story] FORCED r/{pick['subreddit']}  by {pick['author']}")
            print(f"  title: {pick['title'][:100]}")
            print(f"  text:  {len(pick['selftext'])} chars")
            return
        except Exception as e:
            print(f"[fetch_story] forced-fetch failed ({e}) — falling back to trending pick", file=sys.stderr)

    # CACHE-FIRST (2026-06-14): pull from the pre-filled story cache → ZERO Reddit hit.
    # This decouples the 4 daily fires from Reddit's RSS rate limit (the recurring 429 that
    # killed fires). Cache is refilled gently once a day (morning_batch → story_cache --fill).
    # Set RR_NO_CACHE=1 to force a live fetch (used by the fill path itself).
    if not os.environ.get("RR_NO_CACHE"):
        try:
            import story_cache
            cached = story_cache.pop()
            if cached:
                json.dump(cached, open(OUT, "w"), indent=2)
                print(f"[fetch_story] using CACHED story r/{cached.get('subreddit')} "
                      f"(no Reddit hit) — {len(story_cache.load())} left in cache")
                print(f"  title: {cached.get('title','')[:100]}")
                return
            print("[fetch_story] cache empty → live fetch (will refill via morning_batch)")
        except Exception as _ce:
            print(f"[fetch_story] cache unavailable ({_ce}) — live fetch", file=sys.stderr)

    # Fallback: if the Reddit cache is empty and a live fetch fails (429, blocked sub,
    # etc.), fall back to a hook pool that a separate idea-generation job keeps fed daily.
    # Synthesizes a story-shaped record so the rest of the pipeline (rewrite_story ->
    # voice -> render) just works without knowing the source was different.
    pool_path = os.path.expanduser("~/RedditReels/data/idea_pool.jsonl")
    if os.environ.get("RR_USE_IDEA_POOL", "1") != "0" and os.path.exists(pool_path):
        try:
            pool = [json.loads(l) for l in open(pool_path) if l.strip()]
            # Dedup against used_stories.json (re-uses pipeline's used-tracking via URL)
            used_p = os.path.expanduser("~/RedditReels/logs/used_stories.json")
            used_urls = set()
            if os.path.exists(used_p):
                used_urls = {u.get("url") for u in json.load(open(used_p))}
            for entry in pool:
                pseudo_url = f"idea-pool://{entry.get('date','?')}/{hash(entry.get('hook',''))}"
                if pseudo_url in used_urls: continue
                hook = entry.get("hook", "").strip()
                if len(hook) < 20: continue
                pick = {
                    "subreddit": "ContentEngine",
                    "author": "ContentEngine",
                    "title": hook[:100],
                    "selftext": hook,
                    "url": pseudo_url,
                    "permalink": pseudo_url,
                    "source": "idea_pool",
                }
                json.dump(pick, open(OUT, "w"), indent=2)
                print(f"[fetch_story] using idea-pool hook (no Reddit hit)")
                print(f"  hook: {hook[:80]}")
                return
            print("[fetch_story] idea pool exhausted / all used — live fetch")
        except Exception as _pe:
            print(f"[fetch_story] idea pool unavailable ({_pe}) — live fetch", file=sys.stderr)

    # 30% of fires hijack TRENDING_SUBS with an 'hour' time filter (riding the viral wave
    # while it's hot); the other 70% use the stable SUBS pool, respecting the blacklist
    # subreddit_winrate maintains below.
    _blacklist = set()
    try:
        _bl_path = pathlib.Path.home() / "PipelineCleanup" / "subreddit_blacklist.json"
        if _bl_path.exists():
            _bd = json.loads(_bl_path.read_text())
            from datetime import datetime as _dt
            if _bd.get("expires", "") > _dt.now().isoformat():
                _blacklist = set(_bd.get("blacklisted_subs", []))
    except Exception: pass
    _stable = [s for s in SUBS if s not in _blacklist]
    # Sample a subset of subs per fetch rather than all ~14 -- cuts Reddit RSS request
    # volume and avoids HTTP 429 rate-limiting. 6 subs x ~15 posts is plenty. Also weights
    # the live-fetch sweep toward the "Am I The Villain?" series cluster (AITA/JustNoMIL/
    # relationship_advice/EntitledPeople/confession/TwoX) so cache-miss fires still read
    # as one channel identity; falls back to a plain sample if no series sub survives
    # the blacklist.
    active_subs = TRENDING_SUBS if USE_TRENDING_POOL else series_active_subs(_stable, k=min(6, len(_stable)))
    if _blacklist:
        print(f"  [filter] blacklist active: {_blacklist}")
    time_filter = "hour" if USE_TRENDING_POOL else None
    if USE_TRENDING_POOL:
        print(f"[fetch_story] TRENDING-HIJACK mode: subs={TRENDING_SUBS} time=hour")
    import time as _t
    pool = []
    for _i, sub in enumerate(active_subs):
        if _i:
            _t.sleep(2.5)  # 2026-06-13: SPACE requests — Reddit burst-429s rapid fetches
        rss = fetch_rss(sub, t=time_filter)
        if not rss: continue
        ents = parse_entries(rss, sub)
        good = [e for e in ents if is_good(e)]
        print(f"  r/{sub}: {len(ents)} entries → {len(good)} usable")
        pool.extend(good)
        if len(pool) >= 6:     # enough candidates — stop early (fewer requests = no 429)
            break
    # 2026-06-07 FIX: the trending pool (news/worldnews/... with time=hour) frequently
    # yields ZERO usable posts after the 700-char filter — news subs are mostly link
    # posts with no selftext. Previously this hard-exited (sys.exit 1) and KILLED the
    # whole fire (that's why 17:30 today produced no upload). Now: if the trending pool
    # is empty, fall back to the reliable stable SUBS pool (default day/week filter).
    if not pool and USE_TRENDING_POOL:
        print("[fetch_story] trending pool empty → falling back to stable SUBS pool")
        for sub in _r_trend.sample(_stable, min(6, len(_stable))):
            rss = fetch_rss(sub, t=None)
            if not rss: continue
            ents = parse_entries(rss, sub)
            good = [e for e in ents if is_good(e)]
            print(f"  r/{sub}: {len(ents)} entries → {len(good)} usable")
            pool.extend(good)
    if not pool:
        print("[fetch_story] FATAL no usable posts", file=sys.stderr)
        sys.exit(1)
    pool.sort(key=lambda p: abs(1500 - len(p["selftext"])))
    # 2026-06-03: boost stories whose title shares words with Reddit trending tags.
    # Trending-aware selection — was just hashtags before, now influences which
    # STORY gets picked. Tags come from ~/.trending_tags.json (refreshed daily).
    try:
        trending_tags = json.load(open(os.path.expanduser("~/.trending_tags.json"))).get("reddit_popular", [])
        trending_words = {t.lstrip("#").lower() for t in trending_tags}
        if trending_words:
            def trend_overlap(p):
                title_words = set(re.findall(r"[a-z']{4,}", p["title"].lower()))
                return -len(title_words & trending_words)  # negative = sort earlier
            pool.sort(key=lambda p: (trend_overlap(p), abs(1500 - len(p["selftext"]))))
    except Exception: pass
    # 2026-06-03 overnight: viral score filter — try top 5 candidates, pick first
    # one with score >= 50 (Claude judges). Falls through to random if all fail.
    try:
        import sys as _ss
        _ss.path.insert(0, str(pathlib.Path.home() / "RedditReels/tools"))
        from story_filter import viral_score
        _cfg = json.load(open(pathlib.Path.home() / "RedditReels/config/credentials.json"))
        _api = _cfg.get("anthropic_api_key", "")
        pick = None
        for cand in pool[:5]:
            sc = viral_score(cand["title"], cand["selftext"], cand["subreddit"], _api)
            print(f"  viral_score r/{cand['subreddit']}: {sc.get('score','?')} — {sc.get('reason','')[:60]}")
            if sc.get("verdict") == "approve" and sc.get("score", 0) >= 50:
                pick = cand
                break
        if pick is None:
            pick = random.choice(pool[:8])
    except Exception as _e:
        print(f"  [filter] fallback to random: {_e}")
        pick = random.choice(pool[:8])

    # 30% chance: switch to a JUICY TOP COMMENT from this post instead
    use_comment = random.random() < COMMENT_SOURCE_PROBABILITY
    if use_comment:
        comments = fetch_top_comments(pick["url"])
        comments = [c for c in comments if c["text"].split()[0].lower() not in {"this", "yes", "no", "lol", "lmao"}]
        if comments:
            comments.sort(key=score_comment, reverse=True)
            best = comments[0]
            print(f"[fetch_story] USING COMMENT instead of post (score={score_comment(best):.1f})")
            # Build a "story" object using the comment as selftext, post title as context
            pick = {
                "subreddit": pick["subreddit"],
                "title": pick["title"],
                "selftext": f"[Top comment by {best['author']} on this post]:\n{best['text']}",
                "url": best["url"] or pick["url"],
                "author": best["author"],
                "source_type": "comment",
                "original_post_url": pick["url"],
            }
        else:
            print(f"[fetch_story] comment-mode wanted but no good comments; using post")
            pick["source_type"] = "post"
    else:
        pick["source_type"] = "post"

    json.dump(pick, open(OUT, "w"), indent=2)
    print(f"[fetch_story] picked r/{pick['subreddit']}  by {pick['author']}  type={pick['source_type']}")
    print(f"  title: {pick['title'][:100]}")
    print(f"  text:  {len(pick['selftext'])} chars")

if __name__ == "__main__":
    main()
