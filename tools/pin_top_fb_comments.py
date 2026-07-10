#!/usr/bin/env python3
"""Finds the highest-liked comment from someone other than the Page itself
on each recent FB reel and pins it -- surfaces "this is the conversation" to
other viewers and rewards whoever left it."""
import os, sys, time, json, pathlib, signal, urllib.request
from datetime import datetime
from typing import List, Dict, Optional

BASE = pathlib.Path.home()
DEBUG_PORT = 9223
LOG = BASE / "PipelineCleanup" / "fb_pin_top.log"
SEEN = BASE / "PipelineCleanup" / "fb_pin_top_seen.json"


def _log(line: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


def _attach_chrome():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read()
    except Exception as e:
        raise RuntimeError(f"Chrome :{DEBUG_PORT} not reachable. {e}")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    return webdriver.Chrome(options=opts)


def _recent_fb_reels(limit: int = 10) -> List[Dict]:
    up = BASE / "RedditReels" / "logs" / "uploads.jsonl"
    if not up.exists(): return []
    items = []
    for line in up.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        if e.get("fb_posted"): items.append(e["fb_posted"])
    return items[-limit:]


def _load_seen() -> set:
    if not SEEN.exists(): return set()
    try: return set(json.loads(SEEN.read_text()))
    except Exception: return set()


def _save_seen(s: set):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(s)))


class _TimeoutErr(Exception): pass
def _alarm(*a): raise _TimeoutErr("hard cap")


def pin_top_on_reel(d, url: str) -> Optional[Dict]:
    """Find highest-liked organic comment on a FB reel and pin it.
    Returns dict with action, or None on skip/failure."""
    try:
        d.get(url)
        time.sleep(4)
    except Exception as e:
        return {"url": url, "action": "skip-nav-fail", "err": str(e)}
    # Quick: if no comments, skip
    if d.execute_script("return /no comments yet|be the first to comment/i.test(document.body.innerText.substring(0, 4000));"):
        return {"url": url, "action": "skip-no-comments"}
    d.execute_script("window.scrollTo(0, 600);")
    time.sleep(1.5)
    # Find comments + their like counts
    comments = d.execute_script("""
    const arts = Array.from(document.querySelectorAll('div[role="article"]'));
    const out = [];
    for (const a of arts) {
        const t = (a.innerText || '').trim();
        if (!t || t.length < 5 || t.length > 600) continue;
        if (t.includes('FrameWise Cinema\\n') || t.startsWith('FrameWise Cinema')) continue;  // skip own
        // Extract like count if present (small number near 'Like' button)
        let likes = 0;
        const m = t.match(/(\\d+)\\s*(?:Like|like)s?/i);
        if (m) likes = parseInt(m[1]);
        const lines = t.split('\\n').filter(s => s.trim());
        const author = lines[0] || '';
        const body = lines.slice(1, 4).join(' ').substring(0, 200);
        out.push({author, body, likes, full: t.substring(0,300)});
    }
    out.sort((a,b) => b.likes - a.likes);
    return JSON.stringify(out.slice(0, 5));
    """)
    try: cmts = json.loads(comments) if comments else []
    except: cmts = []
    if not cmts:
        return {"url": url, "action": "skip-no-organic"}
    top = cmts[0]
    if top["likes"] < 1:
        return {"url": url, "action": "skip-no-likes-yet", "top_author": top["author"]}
    # Try to pin
    pinned = d.execute_script(f"""
    const target_text = {json.dumps(top['body'][:30])};
    const arts = Array.from(document.querySelectorAll('div[role="article"]'));
    for (const a of arts) {{
        if (!(a.innerText||'').includes(target_text)) continue;
        const btns = a.querySelectorAll('div[role="button"][aria-label*="ction" i],div[aria-label*="more" i]');
        for (const b of btns) {{ b.click(); return true; }}
    }}
    return false;
    """)
    if not pinned:
        return {"url": url, "action": "menu-not-found", "top_author": top["author"]}
    time.sleep(1.5)
    clicked = d.execute_script("""
    const items = Array.from(document.querySelectorAll('div[role="menuitem"],span'));
    for (const it of items) {
        const t = (it.innerText||'').trim().toLowerCase();
        if (/^pin/i.test(t) && t.length < 30) { it.click(); return t; }
    }
    return null;
    """)
    time.sleep(2)
    return {"url": url, "action": "pinned" if clicked else "pin-not-clicked",
            "top_author": top["author"], "likes": top["likes"]}


def run(max_reels: int = 10, execute: bool = False, hard_timeout_s: int = 120) -> int:
    reels = _recent_fb_reels(max_reels)
    if not reels:
        _log("no recent FB reels"); return 0
    seen = _load_seen()
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(hard_timeout_s)
    d = None
    pinned_count = 0
    try:
        d = _attach_chrome()
        try:
            d.set_page_load_timeout(15); d.set_script_timeout(8)
        except Exception: pass
        for url in reels:
            if url in seen: continue
            res = pin_top_on_reel(d, url)
            _log(f"  {url[-30:]} → {res.get('action')}  (likes={res.get('likes',0)})")
            if res.get("action") == "pinned":
                seen.add(url)
                pinned_count += 1
    except _TimeoutErr:
        _log(f"   hit hard {hard_timeout_s}s cap")
    finally:
        signal.alarm(0)
        if d:
            try: d.quit()
            except Exception: pass
    _save_seen(seen)
    _log(f"=== summary: pinned={pinned_count} ===")
    return pinned_count


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    run(execute=args.execute)
