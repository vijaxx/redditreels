#!/usr/bin/env python3
"""Facebook engagement automation via Chrome on :9223.

Two things: seeds engagement on a fresh reel with a Page comment right after
it posts (and pins it), and separately walks recent reels replying to
unanswered comments with Claude using the same prompt as the YouTube version.
The reply path is mostly idle for now since there isn't much comment volume
yet, but it's ready as the channel grows. Rumble Shorts don't support comments
at all (auto-redirects to a viewer with no comment UI), so there's no
equivalent there."""

import os, json, time, sys, re, pathlib, urllib.request
from datetime import datetime
from typing import Optional, List, Dict

BASE = pathlib.Path.home()
CFG  = BASE / "RedditReels" / "config" / "credentials.json"
LOG  = BASE / "PipelineCleanup" / "fb_engagement.log"
SEEN = BASE / "PipelineCleanup" / "fb_auto_reply_seen.json"
DEBUG_PORT = 9223

# 2026-07-01 — "Am I The Villain?" series alignment: the spoken hook opens
# "You decide — am I the villain?" and the caption signs off " Verdict in the
# comments — you judge." These fallback baits (used when smart_bait's Claude call
# is unavailable) now COMPLETE that same verdict ritual instead of generic
# "Drop a " — one recognizable, subscribable format across hook/caption/comment.
ENGAGEMENT_BAITS = [
    " Your verdict: villain or not? Drop it ",
    "Villain or totally justified? Comment your verdict ",
    "You be the judge — was OP the villain here? ",
    " Villain /  not the villain — cast your verdict ",
    "Am I the villain? YOU decide — verdict below ",
    "Jury's out  villain or justified? I read every verdict.",
    "Guilty or innocent? Drop your one-word verdict ",
]

SYSTEM_PROMPT_REPLY = """You reply to Facebook Page comments AS THE CREATOR of a Reddit-storytime / cinematic-quote / curiosity-facts channel called FrameWise Cinema (when motivational) / RedditReels (when storytime).

REPLY RULES:
- 1-2 short sentences. Casual, friendly, warm.
- ALWAYS end with a question OR invitation to engage further.
- If commenter shares similar experience → ask a follow-up.
- If commenter expresses emotion (  ) → mirror briefly + ask what triggered it.
- If commenter asks a question → answer + ask one back.
- If hostile/negative → respond gracefully, redirect to a question.
- NEVER use: orgasm, sex, fuck, shit, damn, hell, ass, kill, suicide, murder, porn.
- NEVER use generic openers ("Thanks for watching!"). Be specific.
- Reply in the SAME LANGUAGE as the comment.

OUTPUT: only the reply text. No quotes. No formatting.
If comment is spam/bot/link-drop/impossible, output exactly: SKIP
"""


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


def _claude_reply(comment_text: str) -> Optional[str]:
    try:
        import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic
        cfg = json.loads(CFG.read_text())
        client = Anthropic(api_key=cfg.get("anthropic_api_key", ""))
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=120,
            system=SYSTEM_PROMPT_REPLY,
            messages=[{"role": "user", "content": f"COMMENT:\n{comment_text}"}],
        )
        text = msg.content[0].text.strip()
        if text == "SKIP" or len(text) < 5:
            return None
        # Reuse RR's ad-safety scrubber
        sys.path.insert(0, str(BASE / "RedditReels" / "pipeline"))
        try:
            from rewrite_story import scrub_text
            text, _ = scrub_text(text)
        except Exception: pass
        return text
    except Exception as e:
        print(f"  [claude] err: {e}", file=sys.stderr)
        return None


def _log(line: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()}  {line}\n")
    print(line)


# ─────────────────────────────  ENGAGEMENT BAIT  ─────────────────────────────
class _BaitTimeout(Exception): pass


def _bait_alarm(*a, **kw): raise _BaitTimeout("post_engagement_bait hit hard 45s cap")


def post_engagement_bait(reel_url: str, pin: bool = True, hard_timeout_s: int = 45,
                          story_ctx: dict = None) -> bool:
    """Post a Page comment on a fresh reel (seeds engagement). Optionally pin it.
    Called right after FB upload completes in redditreels.py.

    2026-06-03 overnight: story_ctx={title, hook, narration} enables SMART context-aware
    bait via tools/smart_bait.py (Claude generates story-specific question that
    triggers reply chains). Falls back to ENGAGEMENT_BAITS rotation if not provided.
    """
    import random, signal
    bait = None
    if story_ctx:
        try:
            import sys as _s, json as _j
            from pathlib import Path as _P
            _s.path.insert(0, str(_P.home() / "RedditReels/tools"))
            from smart_bait import generate_bait
            cfg = _j.loads((_P.home() / "RedditReels/config/credentials.json").read_text())
            bait = generate_bait(story_ctx.get("title",""), story_ctx.get("hook",""),
                                  story_ctx.get("narration",""), cfg.get("anthropic_api_key", ""))
        except Exception: pass
    if not bait:
        bait = random.choice(ENGAGEMENT_BAITS)
    # Hard timeout via signal — works since we run on main thread of parent pipeline
    signal.signal(signal.SIGALRM, _bait_alarm)
    signal.alarm(hard_timeout_s)
    d = None
    try:
        d = _attach_chrome()
        # Selenium-level timeouts so individual steps can't block forever
        try:
            d.set_page_load_timeout(20)
            d.set_script_timeout(10)
        except Exception: pass
        try:
            d.get(reel_url)
        except Exception as e:
            _log(f"   page load timeout on {reel_url}: {e}")
            return False
        time.sleep(4)  # let FB hydrate (was 6 — trim 2s)
        # Find the "Comment as FrameWise Cinema" contenteditable
        ok = d.execute_script(f"""
        const inputs = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
        for (const inp of inputs) {{
            const ph = inp.getAttribute('aria-placeholder') || inp.getAttribute('aria-label') || '';
            if (/^Comment as|^Write a comment|^Add a comment/i.test(ph) || /comment/i.test(ph)) {{
                inp.focus();
                inp.scrollIntoView({{block:'center'}});
                return true;
            }}
        }}
        return false;
        """)
        if not ok:
            _log(f"   no comment input found on {reel_url}")
            return False
        # Type via clipboard paste (more reliable than send_keys for FB's React inputs)
        import subprocess
        subprocess.run(["pbcopy"], input=bait.encode(), check=True)
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(d).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
        time.sleep(2)
        # Press Enter to submit
        ActionChains(d).send_keys(Keys.RETURN).perform()
        time.sleep(4)
        _log(f"   posted bait: '{bait}' on {reel_url}")
        # Pin: find our just-posted comment, open 3-dot menu, click Pin
        if pin:
            time.sleep(2)
            pinned = d.execute_script("""
            // Find our own comment (with our Page name above the bait text)
            const all = Array.from(document.querySelectorAll('div[role="article"]'));
            for (const art of all) {
                if ((art.innerText||'').includes('FrameWise Cinema') && (art.innerText||'').length < 300) {
                    // Find action button (3 dots) within this comment
                    const btns = art.querySelectorAll('div[role="button"][aria-label*="ction" i],div[aria-label*="more" i]');
                    for (const b of btns) {
                        b.click();
                        return true;
                    }
                }
            }
            return false;
            """)
            if pinned:
                time.sleep(2)
                # Look for "Pin comment" in the dropdown
                d.execute_script("""
                const items = Array.from(document.querySelectorAll('div[role="menuitem"],span'));
                for (const it of items) {
                    const t = (it.innerText||'').trim().toLowerCase();
                    if (/^pin/i.test(t) && t.length < 30) {
                        it.click(); return true;
                    }
                }
                return false;
                """)
                time.sleep(2)
                _log(f"   pinned bait comment")
        return True
    except _BaitTimeout as e:
        _log(f"   post_engagement_bait HARD-TIMEOUT after {hard_timeout_s}s — bait may or may not have landed, parent pipeline unblocked")
        return False
    except Exception as e:
        _log(f"   post_engagement_bait failed: {e}")
        return False
    finally:
        signal.alarm(0)  # cancel alarm no matter what
        if d is not None:
            try: d.quit()
            except Exception: pass


# ─────────────────────────────  AUTO-REPLY  ─────────────────────────────
def _load_seen() -> set:
    if not SEEN.exists(): return set()
    try: return set(json.loads(SEEN.read_text()))
    except Exception: return set()


def _save_seen(s: set):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(s)))


def _recent_fb_reels(limit: int = 10) -> List[Dict]:
    """Pull recent FB reels from uploads.jsonl."""
    up = BASE / "RedditReels" / "logs" / "uploads.jsonl"
    if not up.exists(): return []
    items = []
    for line in up.read_text().splitlines():
        try: e = json.loads(line)
        except: continue
        if e.get("fb_posted"):
            items.append({"url": e["fb_posted"], "ts": e.get("ts"), "title": e.get("title")})
    return items[-limit:]


def auto_reply_to_recent(max_reels: int = 10, max_per_reel: int = 5, execute: bool = False) -> int:
    """Walk recent FB reels, read comments, reply to new ones via Claude.
    PERF FIX 2026-06-01: previous version took 67s on a 0-comment scan because each
    reel got 5s page-load + 2s scroll = 7s × 10 reels = 70s minimum. Now does a
    FAST-PATH first: check 'No comments yet' text right after page load, skip if so.
    Drops 0-comment runs from ~67s to ~25s."""
    reels = _recent_fb_reels(max_reels)
    if not reels:
        _log("auto_reply: no recent FB reels in uploads.jsonl"); return 0
    seen = _load_seen()
    d = _attach_chrome()
    # Per-step Selenium timeouts so a bad reel can't block forever
    try:
        d.set_page_load_timeout(15)
        d.set_script_timeout(8)
    except Exception: pass
    posted = 0
    scanned = 0
    fast_skipped = 0
    try:
        for reel in reels:
            url = reel["url"]
            try:
                d.get(url)
                time.sleep(2.5)   # was 5s — half is enough for the empty-comments text to render
            except Exception as e:
                _log(f"  skip {url}: nav failed: {e}"); continue
            scanned += 1
            # FAST PATH: if "No comments yet" is on the page, skip entirely
            try:
                no_comments = d.execute_script(
                    "return /no comments yet|be the first to comment/i.test(document.body.innerText.substring(0, 4000));"
                )
            except Exception:
                no_comments = False
            if no_comments:
                fast_skipped += 1
                continue
            # Scroll to comments section
            d.execute_script("window.scrollTo(0, 600);")
            time.sleep(1.5)   # was 2s
            # Collect comment-thread articles
            comments = d.execute_script("""
            const out = [];
            const arts = Array.from(document.querySelectorAll('div[role="article"]'));
            for (const a of arts) {
                const t = (a.innerText || '').trim();
                if (!t || t.length < 10 || t.length > 400) continue;
                // Skip own-Page comments
                if (t.includes('FrameWise Cinema\\n') || t.startsWith('FrameWise Cinema')) continue;
                // Get author + text — first line usually author
                const lines = t.split('\\n').filter(s => s.trim());
                if (lines.length < 2) continue;
                const author = lines[0];
                const body = lines.slice(1).join(' ').substring(0, 300);
                // Use first 50 chars of body as a stable id (no native id available)
                const id = author + '::' + body.substring(0, 50);
                out.push({id, author, body});
            }
            return JSON.stringify(out.slice(0, 8));
            """)
            try:
                cmts = json.loads(comments) if comments else []
            except Exception:
                cmts = []
            replied_here = 0
            for c in cmts:
                if c["id"] in seen: continue
                if replied_here >= max_per_reel: break
                seen.add(c["id"])
                reply = _claude_reply(c["body"])
                if not reply:
                    _log(f"  SKIP {url[:60]} ← '{c['body'][:50]}'")
                    continue
                _log(f"  {'POST' if execute else 'WOULD-POST'} {url[:60]} ← '{c['body'][:40]}' ↳ '{reply[:70]}'")
                if execute:
                    try:
                        # Find this comment's reply button + click
                        d.execute_script(f"""
                        const arts = Array.from(document.querySelectorAll('div[role="article"]'));
                        for (const a of arts) {{
                            if ((a.innerText||'').includes({json.dumps(c['body'][:30])})) {{
                                const btns = a.querySelectorAll('div[role="button"]');
                                for (const b of btns) {{
                                    if (/^reply$/i.test((b.innerText||'').trim())) {{
                                        b.scrollIntoView({{block:'center'}}); b.click();
                                        return;
                                    }}
                                }}
                            }}
                        }}
                        """)
                        time.sleep(2)
                        import subprocess
                        subprocess.run(["pbcopy"], input=reply.encode(), check=True)
                        from selenium.webdriver.common.keys import Keys
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(d).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
                        time.sleep(1)
                        ActionChains(d).send_keys(Keys.RETURN).perform()
                        time.sleep(3)
                        posted += 1
                        replied_here += 1
                    except Exception as e:
                        _log(f"    POST FAILED: {e}")
    finally:
        try: d.quit()
        except Exception: pass
    _save_seen(seen)
    _log(f"=== summary: scanned={scanned} fast_skip={fast_skipped} {'posted' if execute else 'would-post'}={posted} seen={len(seen)} ===")
    return posted


# ─────────────────────────────  CLI  ─────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--reply", action="store_true", help="run auto-reply scan")
    ap.add_argument("--bait", help="post engagement-bait comment on this reel URL")
    ap.add_argument("--execute", action="store_true", help="actually post (default = dry-run)")
    args = ap.parse_args()
    if args.bait:
        ok = post_engagement_bait(args.bait, pin=True)
        sys.exit(0 if ok else 1)
    if args.reply:
        auto_reply_to_recent(execute=args.execute)
