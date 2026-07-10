#!/usr/bin/env python3
"""
instagram_chrome.py — Instagram Reels upload via Chrome :9223.

CAUTION: IG bot detection is also aggressive. Web uploader was added 2023.
Selenium works but breaks often when IG updates UI.

REQUIREMENTS:
- Manual one-time login: instagram.com via Chrome :9223 → log into target account
- IG cookies persist in profile

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
    """Upload Reel via instagram.com web. Returns URL or None."""
    import signal
    class _T(Exception): pass
    def _a(*a, **k): raise _T()
    signal.signal(signal.SIGALRM, _a)
    signal.alarm(240)

    d = None
    try:
        d = _attach()
        d.set_page_load_timeout(30)
        d.set_script_timeout(15)

        d.get("https://www.instagram.com/")
        time.sleep(8)

        # 1. Click "Create" / "+" button in left nav
        from selenium.webdriver.common.by import By
        clicked = d.execute_script("""
        const btns = Array.from(document.querySelectorAll('a, div[role="button"], svg'));
        for (const b of btns) {
            const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
            if (lbl.includes('new post') || lbl.includes('create')) {
                let el = b;
                while (el && el.tagName !== 'A' && el.getAttribute('role') !== 'button')
                    el = el.parentElement;
                if (el) { el.click(); return true; }
            }
        }
        return false;
        """)
        if not clicked:
            print("  [ig] no create button — likely not logged in")
            return None
        time.sleep(3)

        # 2. Look for "Post" option in dropdown
        d.execute_script("""
        const els = document.querySelectorAll('a, div[role="button"], span');
        for (const e of els) {
            const t = (e.innerText || '').trim();
            if (/^Post$/i.test(t)) { e.click(); return; }
        }
        """)
        time.sleep(3)

        # 3. Find file input
        try:
            file_input = d.find_element(By.CSS_SELECTOR, "input[type='file']")
            file_input.send_keys(str(pathlib.Path(video_path).absolute()))
            print("  [ig] file selected")
        except Exception as e:
            print(f"  [ig] file input not found: {e}")
            return None
        time.sleep(8)

        # 4. Click Next twice (crop screen, then filter screen)
        for step_name in ["crop", "filter"]:
            try:
                d.execute_script("""
                const btns = document.querySelectorAll('button, div[role="button"]');
                for (const b of btns) {
                    if (/^next$/i.test((b.innerText || '').trim())) {
                        b.click(); return;
                    }
                }
                """)
                time.sleep(3)
            except Exception: pass

        # 5. Caption (and tags)
        caption = (description or title)[:2200]
        tag_str = " " + " ".join("#" + t.strip("#").replace(" ", "") for t in (tags or [])[:30])
        caption = (caption + tag_str)[:2200]
        try:
            d.execute_script("""
            const tas = document.querySelectorAll('textarea, div[contenteditable="true"]');
            for (const ta of tas) {
                const ph = ta.getAttribute('aria-label') || ta.getAttribute('placeholder') || '';
                if (/caption|write/i.test(ph)) {
                    ta.focus(); return;
                }
            }
            """)
            import subprocess
            subprocess.run(["pbcopy"], input=caption.encode(), check=True)
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(d).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
            print("  [ig] caption pasted")
            time.sleep(2)
        except Exception as e:
            print(f"  [ig] caption skip: {e}")

        # 6. Share button
        try:
            d.execute_script("""
            const btns = document.querySelectorAll('button, div[role="button"]');
            for (const b of btns) {
                if (/^share$/i.test((b.innerText || '').trim())) {
                    b.click(); return;
                }
            }
            """)
            print("  [ig] Share clicked, waiting for confirmation...")
            time.sleep(15)
        except Exception as e:
            print(f"  [ig] share failed: {e}")
            return None

        return d.current_url

    except _T:
        print("  [ig] HARD TIMEOUT 240s")
        return None
    except Exception as e:
        print(f"  [ig] upload failed: {e}")
        return None
    finally:
        signal.alarm(0)
        if d:
            try: d.quit()
            except: pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: instagram_chrome.py <video_path>")
        sys.exit(1)
    cfg = json.loads(pathlib.Path("~/RedditReels/config/credentials.json").expanduser().read_text())
    r = upload(sys.argv[1], "Test upload", "Testing IG", ["test"], cfg)
    print(f"Result: {r}")
