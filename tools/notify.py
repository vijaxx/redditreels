#!/usr/bin/env python3
"""Push notifications on key pipeline events.

Tries Telegram first (needs telegram_bot_token + telegram_chat_id in creds),
falls back to a native macOS notification, and always writes to a local log
regardless of whether either channel is configured."""
import json, pathlib, subprocess, urllib.request, urllib.parse
from datetime import datetime

CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"
LOG = pathlib.Path.home() / "PipelineCleanup" / "notifications.log"


def _log(title: str, body: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()}  [{title}] {body[:200]}\n")


def _telegram(title: str, body: str) -> bool:
    try:
        cfg = json.loads(CREDS.read_text())
        tok = cfg.get("telegram_bot_token")
        chat = cfg.get("telegram_chat_id")
        if not (tok and chat):
            return False
        msg = f"*{title}*\n{body}"
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": str(chat), "text": msg, "parse_mode": "Markdown"
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10).read()
        return True
    except Exception:
        return False


def _mac_notify(title: str, body: str) -> bool:
    try:
        # Escape double quotes for AppleScript
        safe_t = title.replace('"', '\\"')[:80]
        safe_b = body.replace('"', '\\"')[:200]
        script = f'display notification "{safe_b}" with title "{safe_t}"'
        subprocess.run(["osascript", "-e", script], timeout=5)
        return True
    except Exception:
        return False


def notify(title: str, body: str, urgency: str = "normal") -> dict:
    """Send notification via all configured channels."""
    _log(title, body)
    out = {"telegram": _telegram(title, body), "mac": _mac_notify(title, body)}
    return out


if __name__ == "__main__":
    import sys
    title = sys.argv[1] if len(sys.argv) > 1 else "Test notification"
    body = sys.argv[2] if len(sys.argv) > 2 else "If you see this, notify.py works"
    r = notify(title, body)
    print(json.dumps(r, indent=2))
