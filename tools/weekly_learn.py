#!/usr/bin/env python3
"""
weekly_learn.py — analyze last 7 days of uploads, identify what worked, write insights.

Runs every Sunday 23:00 IST. Output: ~/PipelineCleanup/weekly_insights.json + .md report.

The insights are read by the rewriter prompts on the NEXT week's pipeline runs — the
prompt builder appends a "what worked last week" section. Over months, the pipeline
auto-tunes toward what your audience actually wants.
"""
from __future__ import annotations
import json, os, pathlib, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS_PATH = pathlib.Path(os.path.expanduser("~/RedditReels/config/credentials.json"))
INSIGHTS_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/weekly_insights.json"))
REPORT_PATH = pathlib.Path(os.path.expanduser("~/PipelineCleanup/weekly_insights.md"))
RR_UPLOADS = pathlib.Path(os.path.expanduser("~/RedditReels/logs/uploads.jsonl"))


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS_PATH.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def fetch_recent_videos_with_stats(yt, days=7):
    """All videos uploaded in last `days` days + their stats."""
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    pl_req = yt.playlistItems().list(part="contentDetails,snippet", playlistId=uploads, maxResults=50)
    while pl_req is not None:
        resp = pl_req.execute()
        ids = []
        meta_by_id = {}
        for it in resp.get("items", []):
            published = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
            if published < cutoff: continue
            vid = it["contentDetails"]["videoId"]
            ids.append(vid)
            meta_by_id[vid] = {"title": it["snippet"]["title"], "published": published.isoformat()}
        if ids:
            sresp = yt.videos().list(part="statistics,snippet", id=",".join(ids)).execute()
            for it in sresp.get("items", []):
                vid = it["id"]
                stats = it.get("statistics", {})
                out.append({
                    "video_id": vid,
                    "title": meta_by_id[vid]["title"],
                    "published": meta_by_id[vid]["published"],
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                })
        pl_req = yt.playlistItems().list_next(pl_req, resp)
    return out


def join_with_rr_uploads(stats_list):
    """Match YT videos with their RR upload log entry. Adds: sub, ad_safe,
    plus FB+Rumble URLs for cross-platform view aggregation."""
    if not RR_UPLOADS.exists(): return stats_list
    rr = {}
    for line in RR_UPLOADS.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        vid = e.get("yt_video_id")
        if vid: rr[vid] = e
    for s in stats_list:
        if s["video_id"] in rr:
            entry = rr[s["video_id"]]
            s["sub"] = entry.get("sub")
            s["ad_safe"] = (entry.get("ad_safe_estimate") or {}).get("ad_safe")
            s["scrubbed_words"] = entry.get("ad_safe_scrubbed", [])
            s["fb_url"] = entry.get("fb_posted") or (entry.get("results") or {}).get("facebook")
            s["rumble_url"] = entry.get("rumble_url") or (entry.get("results") or {}).get("rumble")
    return stats_list


def enrich_with_cross_platform_views(stats_list):
    """Augment each video with FB + Rumble views. Adds: fb_views, rumble_views,
    total_views. Added 2026-05-31 so weekly_learn doesn't judge cross-posted videos
    by YT views alone.

    2026-07-05 REWRITE: the old version required Chrome :9223 to be up just to
    START — if it wasn't, BOTH FB and Rumble enrichment were skipped entirely for
    every video (this is why fb_views/rumble_views were null on all 228 recorded
    rows: Chrome happened to be down whenever this ran). Now each platform tries
    its reliable, Chrome-FREE path FIRST (FB Graph API with a Page token; Rumble's
    public video page, no login needed) and only falls back to the fragile
    Chrome-dependent scrapers if that fails AND Chrome happens to be reachable.
    """
    sys.path.insert(0, os.path.expanduser("~/RedditReels/tools"))
    _fb_token = None
    try:
        _fb_token = json.loads(pathlib.Path(os.path.expanduser(
            "~/RedditReels/config/credentials.json")).read_text()
        ).get("facebook_page_access_token")
    except Exception:
        _fb_token = None
    try:
        from view_scrapers import (attach_chrome, get_facebook_views,
                                   get_facebook_views_graph, get_rumble_views,
                                   get_rumble_views_public)
    except Exception as e:
        print(f"  ⚠ view_scrapers unavailable ({e}) — skipping cross-platform enrichment")
        for s in stats_list:
            s["total_views"] = s.get("views", 0)
        return stats_list

    driver = None  # lazily attached only if a Chrome-dependent fallback is needed
    def _driver():
        nonlocal driver
        if driver is None:
            driver = attach_chrome()
        return driver

    for s in stats_list:
        yt_v = s.get("views", 0) or 0
        fb_v = rum_v = None
        if s.get("fb_url"):
            if _fb_token:                                        # reliable, no Chrome
                fb_v = get_facebook_views_graph(s["fb_url"], _fb_token)
            if fb_v is None:                                      # fragile fallback
                try:
                    fb_v = get_facebook_views(s["fb_url"], driver=_driver())
                except Exception as e:
                    print(f"  ⚠ FB fallback unavailable for {s.get('video_id')}: {e}")
        if s.get("rumble_url"):
            rum_v = get_rumble_views_public(s["rumble_url"])       # reliable, no Chrome
        if rum_v is None and (s.get("rumble_url") or s.get("title")):
            try:                                                  # fragile fallback
                rum_v = get_rumble_views(s.get("rumble_url"), driver=_driver(),
                                         title_hint=s.get("title"))
            except Exception as e:
                print(f"  ⚠ Rumble fallback unavailable for {s.get('video_id')}: {e}")
        s["fb_views"] = fb_v
        s["rumble_views"] = rum_v
        s["total_views"] = yt_v + (fb_v or 0) + (rum_v or 0)
        print(f"    {s['video_id']}  YT={yt_v} FB={fb_v} RUM={rum_v} → total={s['total_views']}  {s['title'][:40]}")
    if driver is not None:
        try: driver.quit()
        except Exception: pass
    return stats_list


def claude_insight(data: list) -> dict:
    import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
    cfg = json.loads(CREDS_PATH.read_text())
    client = Anthropic(api_key=cfg.get("anthropic_api_key", ""))
    prompt = f"""You're a cross-platform Shorts/Reels growth analyst. Given last-7-days video performance data across YouTube + Facebook Reels + Rumble, identify ACTIONABLE patterns that the AI rewriter should follow next week to grow TOTAL cross-platform views (not just YouTube). Note each row has views (YT), fb_views, rumble_views, total_views — judge by total_views, and call out cases where a video flopped on YT but did well on FB/Rumble (or vice versa).

DATA (one row per video):
{json.dumps(data, indent=1)}

OUTPUT STRICT JSON:
{{
  "summary": "2-3 sentence plain-English summary of the week",
  "winning_title_patterns": ["specific patterns that correlated with high views"],
  "losing_title_patterns": ["specific patterns that flopped"],
  "best_subreddits": ["subs ranked by avg views per post"],
  "worst_subreddits": ["subs to deprioritize"],
  "winning_hook_words": ["words that recurred in high-view titles"],
  "ad_safety_impact": "did green-tier videos outperform yellow? by how much?",
  "rewriter_directives": [
    "1-3 short imperative instructions to add to the rewrite system prompt next week",
    "e.g. 'Prefer r/X over r/Y. Open titles with numbered reveals.'"
  ],
  "confidence": "low|medium|high (more data = higher confidence; <10 videos = low)"
}}
"""
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1200,
        messages=[{"role":"user","content":prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}")+1]
    try:
        return json.loads(text)
    except Exception as e:
        return {"error": f"parse failed: {e}", "raw": text[:500]}


def main():
    yt = _yt()
    print("Fetching last 7 days of videos + stats...")
    vids = fetch_recent_videos_with_stats(yt, days=7)
    print(f"  found {len(vids)} videos")
    vids = join_with_rr_uploads(vids)
    if not vids:
        print("No videos in window — skipping analysis"); return

    print("Enriching with FB + Rumble views (cross-platform analysis)...")
    vids = enrich_with_cross_platform_views(vids)

    print("Asking Claude for insights...")
    insights = claude_insight(vids)
    insights["analyzed_at"] = datetime.now().isoformat()
    insights["videos_analyzed"] = len(vids)
    insights["raw_data"] = vids

    INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INSIGHTS_PATH.write_text(json.dumps(insights, indent=2))
    print(f"✓ insights written → {INSIGHTS_PATH}")

    # Feed the shared org COLLECTIVE MEMORY so every engine (PinForge, AffiliReels,
    # DropEngine) inherits RR's fresh, real-data learnings — not just RR.
    try:
        import sys as _sys
        _sys.path.insert(0, str(pathlib.Path.home() / ".project-agents"))
        import collective as _cm
        wins = insights.get("winning_title_patterns", [])[:4]
        loses = insights.get("losing_title_patterns", [])[:3]
        reels = ["WINNING hook shape: curiosity-gap 'What happens when [ALLCAPS]…' "
                 "/ 'Villain?' — open the gap, don't resolve it in the hook."]
        if wins:
            reels.append("Fresh winning patterns (RR real data): " + "; ".join(wins))
        if loses:
            reels.append("Avoid (RR real data): " + "; ".join(loses))
        reels.append("First frame must pose a question or stakes-y claim — no slow build.")
        d = _cm._load()
        d["reels"] = reels
        d["_updated"] = insights.get("analyzed_at", "")[:10]
        _cm.STORE.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print("✓ collective memory updated (reels) → all engines inherit")
    except Exception as e:
        print(f"collective memory update skipped: {e}")

    # Pretty markdown report
    lines = [f"# Weekly Insights — {insights['analyzed_at']}", ""]
    lines.append(f"**{len(vids)} videos analyzed**  confidence={insights.get('confidence','?')}")
    lines.append("")
    if "summary" in insights:
        lines += ["## Summary", insights["summary"], ""]
    for k in ["winning_title_patterns","losing_title_patterns","best_subreddits",
              "worst_subreddits","winning_hook_words"]:
        if k in insights:
            lines.append(f"## {k.replace('_',' ').title()}")
            for it in insights[k]: lines.append(f"- {it}")
            lines.append("")
    if "rewriter_directives" in insights:
        lines.append("## Directives for next week's rewriter (auto-appended to prompts)")
        for d in insights["rewriter_directives"]: lines.append(f"- {d}")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"✓ markdown report → {REPORT_PATH}")


if __name__ == "__main__":
    main()
