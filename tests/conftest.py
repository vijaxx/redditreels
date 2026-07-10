"""A few modules read credentials.json at import time (before pytest ever gets to
a test function) -- and they read it from a hardcoded ~/RedditReels/config path,
not a path relative to wherever this repo is actually checked out. CI has no real
credentials, and shouldn't need any to exercise pure logic like scrub_text(), so
this drops a placeholder copy of credentials.example.json at that exact hardcoded
path -- but only if nothing real is already sitting there. Never touches an
existing credentials.json, at either location.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "credentials.example.json"

for creds_dir in (ROOT / "config", Path.home() / "RedditReels" / "config"):
    creds_path = creds_dir / "credentials.json"
    if not creds_path.exists() and EXAMPLE.exists():
        creds_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE, creds_path)
