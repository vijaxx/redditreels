#!/usr/bin/env python3
"""Fetch a satisfying/gameplay-style background loop from Pixabay (free)."""
import json, os, pathlib, sys, urllib.parse, urllib.request

import os, pathlib
ROOT = pathlib.Path(os.environ.get('RR_ROOT', os.path.expanduser('~/RedditReels')))
WORK = pathlib.Path(os.environ.get('RR_WORK', str(ROOT / 'output')))
WORK.mkdir(parents=True, exist_ok=True)

CREDS = json.load(open(os.path.expanduser("~/RedditReels/config/credentials.json")))
KEY = CREDS["pixabay_api_key"]

OUT = ROOT / "clips" / "gameplay.mp4"
OUT.parent.mkdir(parents=True, exist_ok=True)

# Curated curiosity-stop queries — these are the formats that dominate Reddit-story reels
QUERIES = [
    "satisfying soap cutting",
    "kinetic sand cutting",
    "minecraft parkour",
    "subway surfers gameplay",
    "satisfying loop",
    "hydraulic press crushing",
    "marble run",
    "parkour video game",
]

def search(q):
    url = (
        f"https://pixabay.com/api/videos/?key={KEY}&q={urllib.parse.quote(q)}"
        f"&per_page=20&safesearch=true&video_type=film"
    )
    return json.load(urllib.request.urlopen(url))["hits"]

def main():
    target_dur_min = 15  # we'll loop if shorter
    target_dur_max = 60
    best = None
    best_score = -1
    for q in QUERIES:
        try:
            hits = search(q)
        except Exception as e:
            print(f"  ! {q}: {e}", file=sys.stderr)
            continue
        # Prefer portrait or square, then long clips
        for h in hits:
            v = h["videos"].get("medium") or h["videos"]["large"]
            dur = h.get("duration", 0)
            if dur < 6: continue
            portrait_bias = 1.0 if v["height"] >= v["width"] else 0.6
            len_bias = min(1.0, dur / 30.0)
            score = portrait_bias * len_bias * (h.get("downloads", 1) ** 0.05)
            if score > best_score:
                best_score = score
                best = (q, h, v)
        print(f"  q='{q}' → {len(hits)} hits")
    if not best:
        print("[fetch_gameplay] FATAL no hits", file=sys.stderr)
        sys.exit(1)
    q, h, v = best
    print(f"[fetch_gameplay] picked id {h['id']} from q='{q}' — {v['width']}x{v['height']} {h['duration']}s by {h.get('user')}")
    req = urllib.request.Request(v["url"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(OUT, "wb") as f:
        f.write(r.read())
    print(f"  saved → {OUT}  ({OUT.stat().st_size/1024/1024:.1f} MB)")
    meta = {"query": q, "id": h["id"], "user": h.get("user"), "page": h.get("pageURL"),
            "src_w": v["width"], "src_h": v["height"], "duration": h["duration"]}
    json.dump(meta, open(OUT.parent / "_gameplay_meta.json", "w"), indent=2)

if __name__ == "__main__":
    main()
