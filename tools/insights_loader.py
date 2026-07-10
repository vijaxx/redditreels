#!/usr/bin/env python3
"""
insights_loader.py — Inject weekly_learn insights into rewriter prompts.

Closes the auto-tuning loop: weekly_learn.py writes Claude's analysis of last 7
days' performance to ~/PipelineCleanup/weekly_insights.json. Without this loader,
the rewriter never read those insights → AI didn't actually learn.

API:
    get_insights_prompt_block() -> str
        Returns a small (200-400 char) directive block to APPEND to any rewriter
        system prompt. Empty string if no insights yet OR if insights are stale.

Added 2026-06-03 to close the AI feedback loop.
"""
import json, pathlib
from datetime import datetime, timedelta

INSIGHTS_PATH = pathlib.Path.home() / "PipelineCleanup" / "weekly_insights.json"
MAX_AGE_DAYS = 14  # ignore insights older than 2 weeks


def get_insights_prompt_block() -> str:
    """Returns text to APPEND to the rewriter system prompt. Empty if N/A."""
    if not INSIGHTS_PATH.exists():
        return ""
    try:
        d = json.loads(INSIGHTS_PATH.read_text())
    except Exception:
        return ""

    # Skip if stale
    analyzed_at = d.get("analyzed_at")
    if analyzed_at:
        try:
            ts = datetime.fromisoformat(analyzed_at)
            if datetime.now() - ts > timedelta(days=MAX_AGE_DAYS):
                return ""
        except Exception:
            pass

    confidence = d.get("confidence", "low")
    if confidence == "low" and d.get("videos_analyzed", 0) < 5:
        return ""  # not enough data to trust

    # Check if FB views were actually enriched. If all/most are None, the LLM only
    # saw YT views (all 0-4) and its "winning pattern" recommendations are unreliable.
    raw = d.get("raw_data", [])
    if raw:
        fb_none_rate = sum(1 for v in raw if v.get("fb_views") is None) / len(raw)
        if fb_none_rate >= 0.8:
            return ""  # ≥80% of videos missing FB data — analysis is YT-only noise

    parts = []
    parts.append("\n\n=== WHAT WORKED LAST WEEK (from cross-platform analytics) ===")
    if d.get("summary"):
        parts.append(f"Summary: {d['summary']}")
    if d.get("winning_title_patterns"):
        wt = ", ".join(d["winning_title_patterns"][:3])
        parts.append(f"WINNING title patterns: {wt}")
    if d.get("losing_title_patterns"):
        lt = ", ".join(d["losing_title_patterns"][:3])
        parts.append(f"AVOID title patterns: {lt}")
    if d.get("best_subreddits"):
        bs = ", ".join(d["best_subreddits"][:3])
        parts.append(f"Best subs: {bs}")
    if d.get("winning_hook_words"):
        hw = ", ".join(d["winning_hook_words"][:6])
        parts.append(f"Hook words that won: {hw}")
    if d.get("rewriter_directives"):
        rd = "; ".join(d["rewriter_directives"][:3])
        parts.append(f"DIRECTIVES: {rd}")
    parts.append(f"(confidence={confidence}, {d.get('videos_analyzed',0)} videos)")
    parts.append("Lean toward the winning patterns. Avoid the losing ones.")
    parts.append("=== END WEEKLY INSIGHTS ===\n")
    return "\n".join(parts)


if __name__ == "__main__":
    block = get_insights_prompt_block()
    if block:
        print(block)
    else:
        print("(no insights available — empty block)")
