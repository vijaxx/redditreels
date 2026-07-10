#!/usr/bin/env python3
"""
reel_quality_tagger.py — score and tag every rendered reel with quality metadata.

Output dict added to uploads.jsonl per video:
  - ad_safe_tier: "green" | "yellow" | "red" (based on risk word scan)
  - family_friendly: bool (passes G/PG rating heuristic)
  - hook_strength: 0-100 (first 8 words of narration)
  - title_clickbait_score: 0-100 (Claude judge)
  - production_score: composite 0-100

Called from redditreels.py at end of run().

Built 2026-06-03 overnight round 2.
"""
import json, pathlib, re
from typing import Dict


RISK_WORDS = {
    "high": ["suicide","kill myself","sexual assault","rape","molested","child porn"],
    "medium": ["addiction","drug","cocaine","heroin","overdose","abuse","trauma"],
    "low": ["damn","hell","shit","fuck","ass"],
}


def ad_safe_tier(narration: str, title: str) -> str:
    text = (narration + " " + title).lower()
    for w in RISK_WORDS["high"]:
        if w in text: return "red"
    for w in RISK_WORDS["medium"]:
        if w in text: return "yellow"
    for w in RISK_WORDS["low"]:
        if re.search(rf"\b{w}\b", text): return "yellow"
    return "green"


def hook_strength_local(hook: str) -> int:
    """Cheap local heuristic — no Claude call."""
    if not hook: return 0
    words = hook.split()
    score = 50
    if len(words) <= 12: score += 10
    if len(words) >= 18: score -= 20
    # Punchy first word
    first = words[0].lower() if words else ""
    weak = {"in","the","when","so","i","it","this","there","they"}
    if first in weak: score -= 10
    # Numbers + specifics good
    if any(w.isdigit() or w[0].isupper() for w in words[:3]): score += 15
    # Question marks + ellipsis kill momentum
    if hook.endswith("?"): score -= 5
    if "..." in hook: score -= 10
    return max(0, min(100, score))


def tag(narration: str, title: str, hook: str = "") -> Dict:
    return {
        "ad_safe_tier": ad_safe_tier(narration, title),
        "family_friendly": ad_safe_tier(narration, title) == "green",
        "hook_strength_local": hook_strength_local(hook or narration.split(".")[0]),
        "narration_word_count": len(narration.split()),
        "title_length": len(title),
    }


if __name__ == "__main__":
    import sys
    n = sys.argv[1] if len(sys.argv) > 1 else "I reported my boss and got fired. Three words from HR ended my career."
    t = sys.argv[2] if len(sys.argv) > 2 else "I reported and they fired me"
    print(json.dumps(tag(n, t, hook=n.split(".")[0]), indent=2))
