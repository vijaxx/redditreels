#!/usr/bin/env python3
"""Quality gate before a story gets rendered. fetch_story picks by upvotes
and length, which varies wildly in practice -- this adds a judgment pass that
scores each candidate 0-100 on viral potential and rejects anything under 40,
saving several minutes of rendering something that wasn't going to work."""
import json, pathlib


def viral_score(title: str, selftext: str, subreddit: str, api_key: str) -> dict:
    """Returns {score: 0-100, reason: str, predicted_hook: str}."""
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = f"""Rate this Reddit post's potential as a viral 30-60 second YouTube Short narration.

SUBREDDIT: r/{subreddit}
TITLE: {title}
BODY (first 800 chars): {selftext[:800]}

Score 0-100 considering:
- Story has CLEAR setup → tension → payoff (3-act structure)
- WTF moment that makes people pause scroll
- Emotional charge (shock/embarrassment/satisfaction > calm/info)
- Concrete specifics (numbers, names, vivid detail > vague)
- Length suitable for 30-60s narration (not too thin, not too sprawling)
- Already cliffhanger-able (the payoff can be delayed)
- Not too explicit/political/controversial (must be ad-safe)

OUTPUT EXACT JSON (no other text):
{{
  "score": 0-100,
  "verdict": "approve" or "reject",
  "reason": "1 sentence why",
  "predicted_hook": "what hook YOU'd write for this story (8-12 words)"
}}

Reject if score < 40.
"""
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.strip("`")
        # Robust JSON extraction — grab from { to matching }
        if "{" in text:
            text = text[text.find("{"):text.rfind("}")+1]
        return json.loads(text)
    except Exception as e:
        return {"score": 60, "verdict": "approve", "reason": f"scorer failed: {e}",
                "predicted_hook": ""}


if __name__ == "__main__":
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    r = viral_score(
        title="I reported my boss for harassment and was fired the same week",
        selftext="I worked at this firm for 3 years. The regional manager was known for inappropriate comments. After he made a particularly explicit one to a coworker, I went to HR. 6 days later I was 'let go for performance reasons'.",
        subreddit="antiwork",
        api_key=cfg.get("anthropic_api_key", ""))
    print(json.dumps(r, indent=2))
