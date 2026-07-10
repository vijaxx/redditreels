#!/usr/bin/env python3
"""
smart_bait.py — generate context-aware engagement-bait comments per video.

Current fb_engagement.ENGAGEMENT_BAITS picks from 7 generic strings ("Drop a 🔥"
etc). Generic bait gets generic engagement. CONTEXT-AWARE bait gets specific
replies. Example:
  Generic:  "Drop a 🔥 if this hit you"
  Smart:    "What would YOU have said if YOUR boss did this? 👇"

Claude writes 3 bait variants per video based on the actual story. Picks the
one most likely to trigger comment + reply chains.

Built 2026-06-03 overnight.
"""
import json, pathlib
from typing import Optional


def generate_bait(title: str, hook: str, narration: str, api_key: str) -> str:
    """Returns a single context-aware bait string for this specific story."""
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Write ONE Facebook comment to pin under this "Am I The Villain?" Reddit-story Short. The reel is a moral-judgment ritual: it OPENS with "You decide — am I the villain?" and the caption signs off "⚖️ Verdict in the comments — you judge." Your pinned comment's job: COMPLETE that ritual and trigger MAXIMUM reply chain (viewers casting verdicts + arguing with each other, not just replying to us).

VIDEO TITLE: {title}
HOOK: {hook}
STORY (first 300 chars): {narration[:300]}

Write a comment that:
  1. Forces a BINARY verdict rooted in THIS specific story — villain or justified / YTA or NTA — anchored to a concrete detail from the story (NOT a generic "what would you do?").
  2. Explicitly invites the viewer to DROP THEIR VERDICT — the recurring series ritual, so every reel reads as the same "you be the judge" format.
  3. May take a mildly provocative side to invite disagreement + reply chains.

RULES:
- 8-22 words (short enough to read, long enough to specify)
- ONE emoji max (⚖️ / 👇 / 💬 fit the verdict theme)
- MUST end with a clear verdict CTA — e.g. "drop your verdict 👇" / "villain or not? 💬"
- Anchor it to a SPECIFIC detail from THIS story (not generic)
- DO NOT use generic phrases like "Drop a 🔥", "Tag someone", "Save this"

OUTPUT: just the bait text, nothing else. No quotes, no preamble."""
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip().strip('"').strip()
        # Strip any leading "Bait:" or similar
        if text.lower().startswith(("bait:", "comment:", "post:")):
            text = text.split(":", 1)[1].strip()
        return text or "⚖️ Villain or not? Drop your verdict 👇"
    except Exception as e:
        print(f"  [smart_bait] fallback: {e}")
        return "⚖️ Villain or not? Drop your verdict 👇"


if __name__ == "__main__":
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    bait = generate_bait(
        title="I reported my boss and got fired",
        hook="Three words from HR ended my three-year career",
        narration="I reported the regional manager for harassment. Within 6 days I was fired for 'performance reasons' even though I had glowing reviews.",
        api_key=cfg.get("anthropic_api_key", ""))
    print(f"Generated bait: {bait!r}")
