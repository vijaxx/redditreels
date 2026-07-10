#!/usr/bin/env python3
"""
affiliate_links.py — inject themed Amazon Associates links into video descriptions.

Earns revenue from clicks/purchases BEFORE YPP/Meta/Rumble payout thresholds.
At 1000 lifetime views with 1% click rate + 4% conversion @ $20 avg = ~$0.80/mo today,
scaling linearly with views.

CONFIG: set "amazon_affiliate_tag" in ~/RedditReels/config/credentials.json
        e.g. "amazon_affiliate_tag": "yourtag-20"
If not set, returns empty string (graceful no-op).

API:
    affiliate_block(subreddit, theme) -> str
        Returns a 3-line markdown block to append to video description, with 1-2
        theme-relevant Amazon product links using the user's affiliate tag.
"""
import json, pathlib
from typing import Optional

CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"

# Curated themed product recommendations (Amazon search URLs with tag injected)
THEME_PRODUCTS = {
    "antiwork": [
        (" Recommended read", "How to Find Fulfilling Work", "How+to+Find+Fulfilling+Work"),
        (" For the commute", "Wireless Noise-Cancelling Headphones", "noise+cancelling+headphones+commute"),
    ],
    "JustNoMIL": [
        (" Recommended read", "Boundaries: When to Say Yes", "boundaries+when+to+say+yes"),
        (" Self-care gift", "Aromatherapy Diffuser", "essential+oil+diffuser"),
    ],
    "relationship_advice": [
        (" Recommended read", "Attached: The New Science of Love", "attached+amir+levine"),
        (" Recommended read", "The 5 Love Languages", "5+love+languages"),
    ],
    "AmItheAsshole": [
        (" Recommended read", "Crucial Conversations", "crucial+conversations+book"),
    ],
    "tifu": [
        (" Recommended read", "The Subtle Art of Not Giving a F*ck", "subtle+art+of+not+giving"),
    ],
    "EntitledPeople": [
        (" Recommended read", "Boundaries Book", "boundaries+cloud+townsend"),
    ],
    "confession": [
        (" Recommended read", "Radical Honesty", "radical+honesty+brad+blanton"),
    ],
    "PettyRevenge": [
        (" Recommended read", "The 48 Laws of Power", "48+laws+of+power"),
    ],
    "MaliciousCompliance": [
        (" Recommended read", "Bullshit Jobs by David Graeber", "bullshit+jobs+graeber"),
    ],
}

DEFAULT_PRODUCTS = [
    (" Recommended read", "Atomic Habits", "atomic+habits+james+clear"),
]


def affiliate_block(subreddit: str, theme: str = "") -> str:
    """Build 2-3 line affiliate block. Returns empty string if no tag configured."""
    try:
        cfg = json.loads(CREDS.read_text())
        tag = cfg.get("amazon_affiliate_tag", "").strip()
    except Exception:
        return ""
    if not tag:
        return ""  # no-op if not configured — graceful

    # Pick 1-2 products for this subreddit
    sub_lower = (subreddit or "").lower()
    products = DEFAULT_PRODUCTS
    for sub_key, prods in THEME_PRODUCTS.items():
        if sub_key.lower() in sub_lower:
            products = prods[:2]
            break

    lines = ["", " Mentioned / inspired:"]
    for emoji_label, name, search in products:
        url = f"https://www.amazon.com/s?k={search}&tag={tag}"
        lines.append(f"{emoji_label}: {name} → {url}")
    lines.append("(As an Amazon Associate I earn from qualifying purchases at no extra cost to you.)")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sub = sys.argv[1] if len(sys.argv) > 1 else "antiwork"
    print(affiliate_block(sub) or "(no amazon_affiliate_tag set in credentials — no-op)")
