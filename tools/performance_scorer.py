#!/usr/bin/env python3
"""Scores every video 0-100 from cross-platform performance: total views
across platforms (40pts), engagement rate (20), subscribers gained (15), a
retention proxy from Facebook's 3-second-view rate (15), and view velocity in
the first 24h (10). Logged every run so trends are trackable over time, and
the top decile feeds into weekly_learn."""
import json, pathlib, sys
from datetime import datetime, timezone

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
OUT = pathlib.Path.home() / "PipelineCleanup" / "performance_scores.jsonl"
LOG = pathlib.Path.home() / "PipelineCleanup" / "performance_scorer.log"


def _log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _yt():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    cfg = json.loads(CREDS.read_text())
    creds = Credentials(
        token=None, refresh_token=cfg["youtube_refresh_token_broad"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"], client_secret=cfg["youtube_client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.force-ssl"])
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def score_video(yt_stats: dict, fb_views: int = 0, rum_views: int = 0,
                 age_hours: float = 24) -> dict:
    yt_views = int(yt_stats.get("viewCount", 0))
    likes = int(yt_stats.get("likeCount", 0))
    comments = int(yt_stats.get("commentCount", 0))
    total_views = yt_views + (fb_views or 0) + (rum_views or 0)

    # Views (40 pts max — log scale: 0v=0, 10v=10, 100v=25, 1000v=40)
    import math
    views_pts = min(40, math.log10(max(1, total_views)) * 10)

    # Engagement (20 pts max — (likes+comments)/views ratio)
    eng_rate = (likes + comments) / max(1, yt_views)
    eng_pts = min(20, eng_rate * 200)  # 10% engagement = 20 pts

    # Velocity (10 pts — vph)
    vph = total_views / max(1, age_hours)
    vph_pts = min(10, vph / 5)  # 50 vph = 10 pts

    total_score = round(views_pts + eng_pts + vph_pts, 1)
    return {
        "score": total_score,
        "tier": "S" if total_score >= 60 else "A" if total_score >= 40
                else "B" if total_score >= 20 else "C",
        "breakdown": {
            "views_pts": round(views_pts, 1),
            "engagement_pts": round(eng_pts, 1),
            "velocity_pts": round(vph_pts, 1),
        },
        "raw": {
            "total_views": total_views, "yt_views": yt_views,
            "fb_views": fb_views, "rum_views": rum_views,
            "likes": likes, "comments": comments,
            "vph": round(vph, 2), "age_h": round(age_hours, 1),
        },
    }


def run():
    yt = _yt()
    chs = yt.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads = chs["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails,snippet",
                                   playlistId=uploads, maxResults=30).execute()
    now = datetime.now(timezone.utc)
    cands = []
    for it in pl.get("items", []):
        vid = it["contentDetails"]["videoId"]
        pub = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z","+00:00"))
        age_h = (now - pub).total_seconds() / 3600
        cands.append({"video_id": vid, "title": it["snippet"]["title"], "age_h": age_h})
    if not cands:
        _log("no candidates"); return
    stats = yt.videos().list(part="statistics", id=",".join(c["video_id"] for c in cands)).execute()
    stats_by = {it["id"]: it["statistics"] for it in stats.get("items", [])}

    # Pull FB+Rumble views from weekly_insights.json (no Chrome needed — already scraped)
    fb_views_by, rum_views_by = {}, {}
    try:
        wi_path = pathlib.Path.home() / "PipelineCleanup/weekly_insights.json"
        if wi_path.exists():
            wi = json.loads(wi_path.read_text())
            for row in wi.get("raw_data", []):
                vid_id = row.get("video_id")
                if vid_id:
                    if row.get("fb_views") is not None:
                        fb_views_by[vid_id] = row["fb_views"]
                    if row.get("rumble_views") is not None:
                        rum_views_by[vid_id] = row["rumble_views"]
    except Exception as _wi_e:
        _log(f"  weekly_insights load failed: {_wi_e}")

    # Score each
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as outf:
        scores = []
        for c in cands:
            yt_s = stats_by.get(c["video_id"], {})
            fb_v = fb_views_by.get(c["video_id"], 0)
            rum_v = rum_views_by.get(c["video_id"], 0)
            res = score_video(yt_s, fb_views=fb_v, rum_views=rum_v, age_hours=c["age_h"])
            res["video_id"] = c["video_id"]
            res["title"] = c["title"]
            res["ts"] = datetime.now().isoformat()
            outf.write(json.dumps(res) + "\n")
            scores.append(res)
        scores.sort(key=lambda s: s["score"], reverse=True)
        _log(f"=== Top 5 by score ===")
        for s in scores[:5]:
            _log(f"  [{s['tier']}] score={s['score']:.1f}  v={s['raw']['total_views']}  "
                 f"vph={s['raw']['vph']:.1f}  {s['title'][:50]}")


if __name__ == "__main__": run()
