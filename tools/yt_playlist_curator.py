#!/usr/bin/env python3
"""Keeps a handful of themed playlists populated by subreddit -- AITA
stories, workplace drama, MIL drama, petty revenge, TIFU disasters, plus a
rolling best-of-the-week. Playlists help watch time per session and the
algorithm seems to favor videos that belong to one over orphans. Run daily."""
import os, sys, json, pathlib
from typing import Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
STATE = pathlib.Path.home() / "PipelineCleanup" / "playlist_state.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "playlist_curator.log"

PLAYLISTS = {
    "best_aita": {
        "title": " Best AITA Reddit Stories",
        "subs": ["AmItheAsshole"],
        "description": "Am I The Asshole? — the most controversial Reddit verdicts. Daily."
    },
    "workplace_drama": {
        "title": " Workplace Drama — Reddit Stories",
        "subs": ["antiwork", "MaliciousCompliance", "IDontWorkHereLady", "EntitledPeople"],
        "description": "Bosses, coworkers, and quitting stories that hit different."
    },
    "mil_drama": {
        "title": " Mother-in-Law Drama — JustNo Stories",
        "subs": ["JustNoMIL", "relationship_advice"],
        "description": "JustNo MIL meltdowns and family tension. Updated daily."
    },
    "revenge_stories": {
        "title": " Petty Revenge & Karma — Reddit",
        "subs": ["pettyrevenge", "ProRevenge", "MaliciousCompliance"],
        "description": "Real revenge stories — the satisfying kind. Justice served."
    },
    "tifu": {
        "title": " TIFU — Today I F*cked Up",
        "subs": ["tifu", "confession"],
        "description": "Embarrassing mistakes & confessions you can't unhear."
    },
}


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


def _load_state() -> dict:
    if not STATE.exists(): return {"playlists": {}, "added": []}
    try: return json.loads(STATE.read_text())
    except: return {"playlists": {}, "added": []}


def _save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def get_or_create_playlist(yt, key: str, info: dict, state: dict) -> Optional[str]:
    pid = state["playlists"].get(key)
    if pid:
        return pid
    try:
        r = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": info["title"], "description": info["description"]},
            "status": {"privacyStatus": "public"}
        }).execute()
        pid = r["id"]
        state["playlists"][key] = pid
        _log(f"   created playlist: {info['title']} → {pid}")
        return pid
    except Exception as e:
        _log(f"   create playlist failed: {e}")
        return None


def add_video_to_playlist(yt, video_id: str, playlist_id: str, state: dict) -> bool:
    key = f"{video_id}:{playlist_id}"
    if key in state["added"]:
        return False
    try:
        yt.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": playlist_id,
                          "resourceId": {"kind": "youtube#video", "videoId": video_id}}
        }).execute()
        state["added"].append(key)
        return True
    except Exception as e:
        _log(f"   add {video_id} → {playlist_id}: {e}")
        return False


def run():
    yt = _yt()
    state = _load_state()

    # 1) Ensure all playlists exist
    pids = {}
    for k, info in PLAYLISTS.items():
        pid = get_or_create_playlist(yt, k, info, state)
        if pid: pids[k] = pid

    # 2) Walk uploads.jsonl, sort each video into matching playlist by subreddit
    up = pathlib.Path.home() / "RedditReels/logs/uploads.jsonl"
    if not up.exists():
        _log("no uploads.jsonl"); _save_state(state); return
    added = 0
    for line in up.read_text().splitlines():
        try:
            e = json.loads(line)
            vid = e.get("yt_video_id")
            sub = (e.get("sub") or "").lower()
            if not vid: continue
            for k, info in PLAYLISTS.items():
                if any(s.lower() == sub for s in info["subs"]):
                    if k in pids and add_video_to_playlist(yt, vid, pids[k], state):
                        added += 1
                        _log(f"   {vid} → {info['title']}")
                    break
        except Exception: continue
    _save_state(state)
    _log(f"=== summary: added {added} videos to playlists ===")


if __name__ == "__main__": run()
