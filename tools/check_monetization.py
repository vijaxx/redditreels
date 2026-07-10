#!/usr/bin/env python3
"""
check_monetization.py — for each recent uploads.jsonl entry that hasn't yet
been checked, query the YouTube Data API for monetizationDetails + status and
update the line in-place.

Why a separate tool: the upload-time refresh token in credentials.json has
scope `youtube.upload` only. `monetizationDetails` requires either:
  - `youtube.readonly`  (status only; no monetization signal directly)
  - `youtubepartner`    (preferred — exposes monetizationDetails.access.allowed)

One-time setup (browser flow):
  1. Run `python3 ~/RedditReels/tools/check_monetization.py --auth`
     This opens a browser, you grant the broader scope, the new refresh token
     is appended to credentials.json under `youtube_refresh_token_broad`.
  2. After that, `python3 ~/RedditReels/tools/check_monetization.py` just runs
     daily via a cron (see com.redditreels.monetization-check.plist).

Without --auth, falls back to status-only signals (uploadStatus, embeddable,
publicStatsViewable) — limited but better than nothing.
"""
from __future__ import annotations
import argparse, json, os, sys, time, pathlib
from datetime import datetime, timezone

BASE = pathlib.Path(os.path.expanduser("~/RedditReels"))
CREDS_FILE = BASE / "config" / "credentials.json"
UPLOADS = BASE / "logs" / "uploads.jsonl"

BROAD_SCOPES = [
    # `youtube` is the parent scope — includes readonly + force-ssl (for comments)
    # AND covers monetizationDetails. Single scope = single consent click.
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def load_cfg():
    return json.loads(CREDS_FILE.read_text())


def save_cfg(cfg):
    CREDS_FILE.write_text(json.dumps(cfg, indent=2))


def auth_broad():
    """One-time browser flow to get a refresh token with monetization-read scope."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    import subprocess, webbrowser
    cfg = load_cfg()
    client_config = {
        "installed": {
            "client_id": cfg["youtube_client_id"],
            "client_secret": cfg["youtube_client_secret"],
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, BROAD_SCOPES)
    # Force Google Chrome on macOS by registering a custom webbrowser controller.
    # `run_local_server(browser=...)` expects a string name registered with webbrowser.
    class _ChromeController(webbrowser.BaseBrowser):
        def open(self, url, new=0, autoraise=True):
            subprocess.Popen(["open", "-a", "Google Chrome", url])
            return True
    webbrowser.register("chrome-mac", None, _ChromeController(), preferred=True)
    creds = flow.run_local_server(
        port=0, prompt="consent", access_type="offline",
        browser="chrome-mac",
    )
    if not creds.refresh_token:
        print("FATAL: no refresh token returned. Re-run with prompt='consent'.")
        sys.exit(2)
    cfg["youtube_refresh_token_broad"] = creds.refresh_token
    save_cfg(cfg)
    print(f"✓ Broad-scope refresh token saved to {CREDS_FILE}")
    print("  Scopes:", BROAD_SCOPES)


def build_yt_client(cfg):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    rt = cfg.get("youtube_refresh_token_broad") or cfg["youtube_refresh_token"]
    creds = Credentials(
        token=None, refresh_token=rt,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["youtube_client_id"],
        client_secret=cfg["youtube_client_secret"],
        scopes=BROAD_SCOPES if cfg.get("youtube_refresh_token_broad") else
               ["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds), bool(cfg.get("youtube_refresh_token_broad"))


def fetch_monetization(yt, video_ids: list, has_broad: bool) -> dict:
    """Returns {video_id: {ad_state, uploadStatus, privacyStatus, ...}}."""
    if not video_ids:
        return {}
    parts = ["status"]
    if has_broad:
        parts.append("monetizationDetails")
    parts = ",".join(parts)
    out = {}
    # YT allows up to 50 IDs per call
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        resp = yt.videos().list(part=parts, id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            vid = item["id"]
            status = item.get("status", {})
            mon = item.get("monetizationDetails", {})
            access = mon.get("access", {})
            ad_state = "unknown"
            if has_broad:
                if access.get("allowed") is True:
                    ad_state = "green"        # fully monetized
                elif access.get("allowed") is False:
                    # YT returns 'exception' list explaining why; treat as restricted
                    ad_state = "yellow_or_red"
            out[vid] = {
                "ad_state": ad_state,
                "uploadStatus": status.get("uploadStatus"),
                "privacyStatus": status.get("privacyStatus"),
                "embeddable": status.get("embeddable"),
                "publicStatsViewable": status.get("publicStatsViewable"),
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", action="store_true", help="one-time browser flow for broad OAuth scope")
    ap.add_argument("--limit", type=int, default=20, help="how many recent unchecked uploads to scan")
    args = ap.parse_args()

    if args.auth:
        auth_broad()
        return

    if not UPLOADS.exists():
        print("no uploads.jsonl — nothing to check")
        return

    # Read entries, find unchecked ones with a YT video ID
    lines = UPLOADS.read_text().splitlines()
    entries = []
    for ln in lines:
        try: entries.append(json.loads(ln))
        except Exception: entries.append(None)

    unchecked = [(i, e) for i, e in enumerate(entries)
                 if e and e.get("yt_video_id") and not e.get("yt_monetization_real")]
    unchecked = unchecked[-args.limit:]
    if not unchecked:
        print("no unchecked uploads with YT video IDs")
        return

    print(f"checking {len(unchecked)} videos...")
    cfg = load_cfg()
    yt, has_broad = build_yt_client(cfg)
    print(f"  using {'BROAD' if has_broad else 'upload-only'} scope")

    video_ids = [e["yt_video_id"] for _, e in unchecked]
    results = fetch_monetization(yt, video_ids, has_broad)

    now_iso = datetime.now(timezone.utc).isoformat()
    updates = 0
    for idx, entry in unchecked:
        vid = entry["yt_video_id"]
        if vid in results:
            entries[idx]["yt_monetization_real"] = results[vid]
            entries[idx]["yt_monetization_checked_at"] = now_iso
            updates += 1
            r = results[vid]
            tier = r.get("ad_state","?")
            est = (entry.get("ad_safe_estimate") or {}).get("ad_safe", "?")
            match = "✓ matches est" if est == tier or (est == "yellow" and tier == "yellow_or_red") else "≠ est"
            print(f"  {vid}  ad={tier:<15s} est={est:<6s} {match}  '{entry.get('title','')[:60]}'")

    # Rewrite file
    UPLOADS.write_text("\n".join(json.dumps(e) if e else "" for e in entries) + ("\n" if entries else ""))
    print(f"updated {updates} entries in {UPLOADS}")


if __name__ == "__main__":
    main()
