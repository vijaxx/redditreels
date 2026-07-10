#!/usr/bin/env python3
"""
hashtag_miner.py — Claude generates 25 high-discoverability hashtags per video
based on its actual title + narration + subreddit + theme.

Mix per video (25 total):
  - 8 evergreen high-volume (#fyp, #viral, #reddit, #storytime, etc.)
  - 5 theme-specific medium-volume (story type: relationship/work/family/etc.)
  - 7 content-specific from Claude analysis of THIS video's narration
  - 5 trending today (from ~/.trending_tags.json)

API:
    mine_hashtags(title, narration, subreddit, theme, anthropic_api_key) -> list[str]
"""
import json, os, pathlib, re
from typing import List

EVERGREEN = ["#shorts", "#fyp", "#foryou", "#viral", "#reddit", "#redditstories",
             "#storytime", "#truestory", "#funny", "#shorts2026", "#reels"]

THEME_TAGS = {
    "antiwork": ["#workdrama","#worklife","#bossfromhell","#workplaceproblems","#fired"],
    "JustNoMIL": ["#mildrama","#familydrama","#inlaws","#momdrama","#marriage"],
    "relationship_advice": ["#relationshipdrama","#breakup","#dating","#couplegoals","#cheating"],
    "AmItheAsshole": ["#aita","#wasitwrong","#judgment","#moraldilemma","#villain"],
    "tifu": ["#fail","#oops","#epicfail","#disaster","#cringe"],
    "EntitledPeople": ["#karen","#entitled","#publicfreakout","#audacity","#nope"],
    "confession": ["#secret","#confession","#truthbomb","#regret","#guilty"],
    "PettyRevenge": ["#revenge","#payback","#karma","#satisfying","#justice"],
    "MaliciousCompliance": ["#maliciouscompliance","#justice","#karma","#rules","#sweetrevenge"],
}


def _trending_tags(n=5) -> List[str]:
    p = pathlib.Path.home() / ".trending_tags.json"
    if not p.exists(): return []
    try:
        d = json.loads(p.read_text())
        return d.get("reddit_popular", [])[:n] + d.get("yt_suggest", [])[:max(0, n-len(d.get("reddit_popular",[])))]
    except: return []


def _content_tags_via_claude(title: str, narration: str, sub: str, theme: str,
                              api_key: str, n: int = 7) -> List[str]:
    """Ask Claude for 7 video-specific hashtags."""
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Generate exactly {n} short hashtags for this Reddit story video.

TITLE: {title}
SUBREDDIT: r/{sub}
THEME: {theme}
NARRATION (first 300 chars): {narration[:300]}

Rules:
- Each tag: lowercase, no spaces, 4-18 chars after the #
- Mix specific (#bosstoldme) with broad (#workrant)
- ONE tag may capture an emotional payoff (#satisfying, #cringe, #unbelievable)
- Avoid #shorts #fyp #viral (already included separately)
- Output ONLY the {n} tags, one per line, with the # prefix
"""
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        tags = re.findall(r"#[a-z0-9]{2,25}", text.lower())
        return tags[:n]
    except Exception as e:
        print(f"  [hashtag_miner] Claude fallback: {e}")
        return []


def mine_hashtags(title: str, narration: str, subreddit: str, theme: str,
                   anthropic_api_key: str) -> List[str]:
    """Return ~25 deduplicated hashtags ranked by relevance."""
    tags = []
    # 8 evergreen
    tags.extend(EVERGREEN[:8])
    # 5 theme-specific
    sub_lower = (subreddit or "").lower()
    for sub_key, theme_tags in THEME_TAGS.items():
        if sub_key.lower() in sub_lower:
            tags.extend(theme_tags[:5])
            break
    # 7 from Claude
    tags.extend(_content_tags_via_claude(title, narration, subreddit, theme, anthropic_api_key))
    # 5 trending
    tags.extend(_trending_tags(5))
    # Dedupe (preserve order) + cap at 25
    seen = set()
    out = []
    for t in tags:
        t = t.lower().strip()
        if t and t not in seen:
            seen.add(t); out.append(t)
        if len(out) >= 25: break
    # 2026-06-03 overnight round 2: filter out burned-out tags
    try:
        from hashtag_rotator import filter_tags
        out = filter_tags(out)
    except Exception: pass
    return out


if __name__ == "__main__":
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    tags = mine_hashtags(
        title="I reported my boss and got fired the same week",
        narration="I worked at this company for 3 years. Then I reported my boss for sexual harassment. Within a week I was fired.",
        subreddit="antiwork", theme="work-drama",
        anthropic_api_key=cfg.get("anthropic_api_key", ""))
    print(f"Generated {len(tags)} tags:")
    for t in tags: print(f"  {t}")
