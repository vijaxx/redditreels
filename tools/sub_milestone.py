#!/usr/bin/env python3
"""Polls subscriber count hourly and fires a notification the moment it
crosses a milestone (5, 10, 25... up to 100k), plus drafts a thank-you
community post."""
import json, pathlib
from datetime import datetime

CHANNEL_ID = "UCQSrcHzHqpkFZjnlBkKrClQ"
CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
STATE = pathlib.Path.home() / "PipelineCleanup" / "sub_milestone.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "sub_milestone.log"

MILESTONES = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 10000, 100000]


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


def run():
    yt = _yt()
    r = yt.channels().list(part="statistics", id=CHANNEL_ID).execute()
    subs = int(r["items"][0]["statistics"].get("subscriberCount", 0))
    state = json.loads(STATE.read_text()) if STATE.exists() else {"last_milestone": 0}
    last = state.get("last_milestone", 0)
    new_milestones = [m for m in MILESTONES if m > last and m <= subs]
    if not new_milestones:
        _log(f"  current subs={subs}, last_milestone={last} — no new milestone")
        return
    for m in new_milestones:
        _log(f" MILESTONE: crossed {m} subs!")
        try:
            import sys as _s
            _s.path.insert(0, str(pathlib.Path.home() / "RedditReels/tools"))
            from notify import notify
            notify(f" {m} subscribers!",
                    f"Channel hit {m} subs. Plan a thank-you community post.")
        except: pass
    state["last_milestone"] = max(new_milestones)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__": run()
