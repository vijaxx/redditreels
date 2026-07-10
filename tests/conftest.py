"""A few modules read config/credentials.json at import time (before pytest ever
gets to a test function). CI has no real credentials -- and shouldn't need any to
exercise pure logic like scrub_text() -- so this drops in a placeholder copy of
credentials.example.json if (and only if) nothing real is already sitting there.
Never touches an existing credentials.json.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDS = ROOT / "config" / "credentials.json"
EXAMPLE = ROOT / "config" / "credentials.example.json"

if not CREDS.exists() and EXAMPLE.exists():
    shutil.copy(EXAMPLE, CREDS)
