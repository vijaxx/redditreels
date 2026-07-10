#!/usr/bin/env python3
"""
tiktok_chrome.py — TikTok upload via Chrome :9223.

CAUTION: TikTok's bot detection is aggressive. Selenium uploads work ~60-70% of
the time but break often when TT changes their UI (~once per 2-3 weeks). This is
a best-effort implementation — wraps every step in try/except, returns None on
fail, never blocks the parent pipeline.

REQUIREMENTS:
- Manual one-time login: open Chrome on :9223 → tiktok.com → log in to user's account
- TT cookies persist in the FrameWiseChrome profile

API: upload(video_path, title, description, tags, cfg) → url or None

Built 2026-06-03 overnight — experimental, NOT wired to live cron yet.
"""
import time, urllib.request, json, pathlib
from typing import Optional


DEBUG_PORT = 9223


def _attach():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read()
    except Exception as e:
        raise RuntimeError(f"Chrome :{DEBUG_PORT} dead: {e}")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    return webdriver.Chrome(options=opts)


def upload(video_path: str, title: str, description: str, tags: list, cfg: dict) -> Optional[str]:
    """Upload to TikTok via the creator portal. Returns video URL or None."""
    import signal
    class _T(Exception): pass
    def _a(*a, **k): raise _T()
    signal.signal(signal.SIGALRM, _a)
    signal.alarm(180)  # hard 3-min cap

    d = None
    try:
        d = _attach()
        d.set_page_load_timeout(30)
        d.set_script_timeout(15)

        # 1. Open creator-center upload page
        d.get("https://www.tiktok.com/creator-center/upload?from=upload")
        time.sleep(8)

        # 2. Find file input (TT uses standard <input type=file>)
        from selenium.webdriver.common.by import By
        file_input = None
        try:
            file_input = d.find_element(By.CSS_SELECTOR, "input[type='file']")
        except Exception:
            print("  [tiktok] no file input found — likely not logged in or UI changed")
            return None
        file_input.send_keys(str(pathlib.Path(video_path).absolute()))
        print("  [tiktok] file selected, waiting for processing...")
        time.sleep(15)

        # 3. Caption (TT requires 2200 char max)
        caption = (description or title)[:2150]
        # Append hashtags as TT-friendly form
        hashtag_str = " " + " ".join("#" + t.strip("#").replace(" ", "") for t in (tags or [])[:5])
        caption = (caption + hashtag_str)[:2200]

        # Find caption editor (it's a div[contenteditable])
        try:
            cap = d.execute_script("""
            const els = document.querySelectorAll('div[contenteditable="true"]');
            for (const e of els) {
                const ph = e.getAttribute('aria-label') || e.getAttribute('placeholder') || '';
                if (/caption|describe|tell/i.test(ph)) {
                    e.focus(); e.scrollIntoView({block:'center'});
                    return true;
                }
            }
            return false;
            """)
            if cap:
                import subprocess
                subprocess.run(["pbcopy"], input=caption.encode(), check=True)
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(d).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
                print("  [tiktok] caption pasted")
                time.sleep(2)
        except Exception as e:
            print(f"  [tiktok] caption step skipped: {e}")

        # 4. Click Post button — wait for video to finish processing first
        for _ in range(20):  # 20 * 5s = 100s max wait
            try:
                post_clicked = d.execute_script("""
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const t = (b.innerText || '').trim();
                    if (/^post$/i.test(t) && !b.disabled) {
                        b.click(); return true;
                    }
                }
                return false;
                """)
                if post_clicked:
                    print("  [tiktok] Post button clicked")
                    break
            except Exception: pass
            time.sleep(5)
        time.sleep(8)

        # 5. After post, TT redirects to a profile/manage page. Extract video URL.
        # Try to find link to the just-posted video
        try:
            url = d.execute_script("""
            const links = document.querySelectorAll('a[href*="/video/"]');
            return links.length ? links[0].href : null;
            """)
            if url:
                print(f"  ✓ TikTok ✅  {url}")
                return url
        except Exception: pass
        # Fallback: report current page
        return d.current_url

    except _T:
        print("  [tiktok] HARD TIMEOUT after 180s — aborting")
        return None
    except Exception as e:
        print(f"  [tiktok] upload failed: {e}")
        return None
    finally:
        signal.alarm(0)
        if d:
            try: d.quit()
            except: pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: tiktok_chrome.py <video_path>")
        sys.exit(1)
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    r = upload(sys.argv[1], "Test upload", "Testing TikTok automation", ["test", "shorts"], cfg)
    print(f"Result: {r}")
