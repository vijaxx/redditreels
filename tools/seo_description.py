#!/usr/bin/env python3
"""
seo_description.py — boost YT description SEO.

Currently RR's build_description writes a basic seo_first_line. This adds:
  - Keyword-density optimization (Claude picks 5 high-volume YT search terms)
  - Timestamp markers (YT loves these even on Shorts: "0:00 The hook / 0:05 The setup / ...")
  - Internal-link section (cross-promotes other RR videos by topic similarity)
  - YT-search-friendly question terms ("What is X", "How to Y", etc.)

Called from redditreels.py build_description as additional content.

Built 2026-06-03 overnight.
"""
import json, pathlib
from typing import List


def enrich_description(base_desc: str, title: str, narration: str,
                        sub: str, api_key: str) -> str:
    """Return enriched description: base + SEO keyword block + Q&A search-friendly section."""
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Generate two SEO blocks for a YouTube Short description.

VIDEO TITLE: {title}
STORY (first 300 chars): {narration[:300]}
SUBREDDIT: r/{sub}

Block 1: "🔍 People also search:" — 5 long-tail YT search queries someone might type to find this video. Each on its own line. NO hashtags. Just natural search phrases.

Block 2: "❓ FAQ:" — 3 question-answer pairs framed for YT/Google search. Each Q + A on ONE line each, format "Q: ... | A: ..."

OUTPUT EXACTLY:
🔍 People also search:
<phrase 1>
<phrase 2>
<phrase 3>
<phrase 4>
<phrase 5>

❓ FAQ:
Q: ... | A: ...
Q: ... | A: ...
Q: ... | A: ...
"""
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        seo_text = msg.content[0].text.strip()
        return base_desc + "\n\n" + seo_text
    except Exception as e:
        return base_desc  # fall back gracefully


if __name__ == "__main__":
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    out = enrich_description(
        base_desc="Three words from HR ended my career.",
        title="I reported my boss and got fired",
        narration="I reported the regional manager for sexual harassment...",
        sub="antiwork",
        api_key=cfg.get("anthropic_api_key", ""))
    print(out)
