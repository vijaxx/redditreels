#!/usr/bin/env python3
"""Facebook Reels posting via the official Graph API — robust replacement for the
browser-automation path (facebook_chrome.py), which is 0/52 because FB keeps changing
the Reels composer UI and it risks the (already-once-flagged) account.

The Graph API Reels flow is a 3-step resumable upload:
  1) START  → POST /{page_id}/video_reels  (upload_phase=start)  → {video_id, upload_url}
  2) UPLOAD → POST {upload_url}  with header Authorization: OAuth <token>  + raw file bytes
  3) FINISH → POST /{page_id}/video_reels  (upload_phase=finish, video_state=PUBLISHED,
              description=<caption>)  → publishes the reel

Requires in config/credentials.json:
  * facebook_page_id              (already set)
  * facebook_page_access_token    (a long-lived PAGE token with pages_manage_posts +
                                   pages_read_engagement — see FB_API_SETUP.md)
"""
import json, time, urllib.request, urllib.parse, urllib.error, pathlib, logging

GRAPH_VERSION_DEFAULT = "v23.0"
log = logging.getLogger("redditreels.fb_api")


def _post_form(url, params, timeout=120):
    """POST application/x-www-form-urlencoded, return parsed JSON (raises on HTTP error)."""
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _upload_bytes(upload_url, file_path, access_token, timeout=600):
    """POST the raw video bytes to the rupload endpoint with the resumable headers."""
    size = pathlib.Path(file_path).stat().st_size
    body = pathlib.Path(file_path).read_bytes()
    req = urllib.request.Request(upload_url, data=body, method="POST")
    req.add_header("Authorization", f"OAuth {access_token}")
    req.add_header("offset", "0")
    req.add_header("file_size", str(size))
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _err_text(e):
    try:
        return e.read().decode()[:400]
    except Exception:
        return str(e)


def can_post(cfg) -> bool:
    return bool(cfg.get("facebook_page_id") and cfg.get("facebook_page_access_token"))


def upload_reel_api(video_path, caption, cfg, _log=None) -> str:
    """Publish a Reel via Graph API. Returns the permalink/id on success, else raises.

    Non-destructive: only this Page's reels endpoint is touched; no browser, no UI clicks.
    """
    L = _log or log
    page_id = cfg["facebook_page_id"]
    token = cfg["facebook_page_access_token"]
    ver = cfg.get("facebook_graph_version", GRAPH_VERSION_DEFAULT)
    base = f"https://graph.facebook.com/{ver}/{page_id}/video_reels"

    # 1) START
    L.info("FB-API: start upload session")
    try:
        start = _post_form(base, {"upload_phase": "start", "access_token": token})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FB-API start failed: {_err_text(e)}")
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not (video_id and upload_url):
        raise RuntimeError(f"FB-API start returned no video_id/upload_url: {start}")

    # 2) UPLOAD bytes
    L.info(f"FB-API: uploading {pathlib.Path(video_path).stat().st_size/1e6:.1f}MB → video_id={video_id}")
    try:
        up = _upload_bytes(upload_url, video_path, token)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FB-API upload failed: {_err_text(e)}")
    if not up.get("success", True):
        raise RuntimeError(f"FB-API upload not acked: {up}")

    # 3) FINISH (publish)
    L.info("FB-API: finish → publish")
    try:
        fin = _post_form(base, {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption or "",
            "access_token": token,
        })
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FB-API finish failed: {_err_text(e)}")
    if not fin.get("success", True):
        raise RuntimeError(f"FB-API finish not acked: {fin}")

    # 4) confirm publish status (best-effort poll)
    status_url = f"https://graph.facebook.com/{ver}/{video_id}"
    for _ in range(6):
        time.sleep(5)
        try:
            q = urllib.parse.urlencode({"fields": "status,permalink_url", "access_token": token})
            with urllib.request.urlopen(f"{status_url}?{q}", timeout=30) as r:
                st = json.loads(r.read().decode())
            phase = (st.get("status") or {}).get("video_status") or (st.get("status") or {}).get("processing_phase")
            perma = st.get("permalink_url")
            if perma or phase in ("ready", "published"):
                url = ("https://www.facebook.com" + perma) if perma and perma.startswith("/") else (perma or f"video_id={video_id}")
                L.info(f"FB-API ✅ published: {url}")
                return url
        except Exception:
            continue
    L.info(f"FB-API ✅ published (status pending) video_id={video_id}")
    return f"fb_video_id={video_id}"


if __name__ == "__main__":
    import sys
    cfg = json.loads((pathlib.Path.home() / "RedditReels/config/credentials.json").read_text())
    if not can_post(cfg):
        print("MISSING facebook_page_id or facebook_page_access_token — see FB_API_SETUP.md")
        sys.exit(2)
    vid = sys.argv[1] if len(sys.argv) > 1 else None
    if not vid:
        print("usage: facebook_api.py <video.mp4> [caption]"); sys.exit(2)
    cap = sys.argv[2] if len(sys.argv) > 2 else "Test reel via Graph API"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(upload_reel_api(vid, cap, cfg))
