"""
Rumble uploader — production version using attached real Chrome.

WHY: Rumble's Cloudflare bot detection and React form widgets defeat both
vanilla Selenium and undetected-chromedriver. The bulletproof workaround is
to drive a REAL Chrome instance that was launched normally (no automation
flags) and attach Selenium to it via the DevTools remote-debugging-port.

ONE-TIME SETUP (run by ensure_chrome.sh — see below):
  1. Launch Chrome with:
       --user-data-dir=$HOME/Library/Application\ Support/FrameWiseChrome
       --remote-debugging-port=9223
       --no-first-run --no-default-browser-check
  2. User manually logs into rumble.com once. Cookies persist in the profile.

DAILY USE:
  upload(video_path, title, description, tags, cfg) — attaches to the running
  Chrome on port 9223 and drives the upload form to completion.
"""

import json, logging, time
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)


def _tick_checkboxes_trusted(d):
    """Tick #crights + #cterms via REAL trusted clicks (Selenium WebElement.click()).
    JS-dispatched clicks have isTrusted=false which Rumble's server-side validation
    rejects — leading to the "please confirm" warning + silent submit failure.

    Strategy:
      1. Make the input visually clickable by removing display:none if present
         (Rumble hides input + uses styled label as the visible UI)
      2. Scroll the label into view
      3. Call WebElement.click() on the LABEL (real OS mouse event)
      4. Verify the input.checked became true after click
      5. Fallback to JS only if Selenium click fails
    """
    out = {}
    for box_id in ['crights', 'cterms']:
        try:
            # First make sure the input isn't display:none (won't be clickable)
            d.execute_script(f"""
                var inp = document.getElementById('{box_id}');
                if(inp){{ inp.style.position = 'static'; inp.style.opacity = '1';
                          inp.style.pointerEvents = 'auto'; }}
            """)
            # Try clicking the label first (Rumble UI pattern: hidden input + styled label)
            lbl = None
            try:
                lbl = d.find_element("css selector", f'label[for="{box_id}"]')
            except Exception:
                pass
            try:
                el = d.find_element("css selector", f"#{box_id}")
            except Exception:
                out[box_id] = 'NOT_FOUND'
                continue

            # Scroll into view first
            try:
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", lbl or el)
                time.sleep(0.3)
            except Exception:
                pass

            # Real trusted click — try label first, fall back to input
            clicked = False
            for target in (lbl, el):
                if target is None: continue
                try:
                    target.click()  # Selenium WebElement.click() = real OS mouse → isTrusted=true
                    clicked = True
                    break
                except Exception:
                    continue

            if not clicked:
                # Last resort: JS click (isTrusted=false but better than nothing)
                d.execute_script(f"""
                    var inp = document.getElementById('{box_id}');
                    if(inp){{ inp.checked = true; inp.click();
                              inp.dispatchEvent(new Event('change',{{bubbles:true}})); }}
                """)

            time.sleep(0.3)
            is_checked = d.execute_script(f"""
                var inp = document.getElementById('{box_id}');
                return inp ? !!inp.checked : false;
            """)
            out[box_id] = 'CHECKED' if is_checked else 'STILL_UNCHECKED'
        except Exception as e:
            out[box_id] = f'ERROR: {e.__class__.__name__}'
    # Also tick any extra visible checkboxes via JS (less critical)
    try:
        d.execute_script("""
            document.querySelectorAll('input[type=checkbox]').forEach(function(b){
                if(b.id === 'crights' || b.id === 'cterms') return;
                if(b.offsetParent !== null && !b.checked){ b.checked = true; b.click(); }
            });
        """)
    except Exception:
        pass
    return out


def _detect_submit_warning(d):
    """After clicking submit, Rumble may show a warning like 'Please confirm you agree
    to the terms'. Returns the warning text if found, else None."""
    try:
        return d.execute_script("""
            // Look for visible error/warning text near the submit button
            var warnings = [];
            var keywords = ['please confirm','please check','must agree','must accept',
                            'must check','please tick','must tick','required','terms of service'];
            document.querySelectorAll('div,span,p,label').forEach(function(el){
                if(el.offsetParent === null) return;
                if(el.children.length > 0) return;  // leaf nodes only
                var t = (el.textContent||'').toLowerCase().trim();
                if(t.length < 4 || t.length > 200) return;
                keywords.forEach(function(k){
                    if(t.indexOf(k) !== -1){ warnings.push(t.substring(0,100)); }
                });
            });
            return warnings.length ? warnings[0] : null;
        """)
    except Exception:
        return None


def _wait_upload_complete(d, timeout: int = 720) -> bool:
    """Block until Rumble's file upload reaches 100% BEFORE we click publish.

    ROOT CAUSE this fixes: clicking #submitForm2 (final publish) while the file is
    still uploading (e.g. 72%) is silently ignored by Rumble — the page never
    advances to the success/URL page and the run "completes but no URL found".
    FrameWise's small clips finished uploading in time by luck; ScrollStop's
    bigger clips (and slow upload bandwidth) did not. So we explicitly wait."""
    start = time.time()
    last = -1
    stable = 0
    while time.time() - start < timeout:
        pct = d.execute_script("""
            var m = (document.body.innerText.match(/(\\d{1,3})\\s*%/g) || []);
            var mx = -1; m.forEach(function(s){ var n = parseInt(s,10); if(n>mx) mx=n; });
            return mx;
        """)
        if pct != last:
            log.info(f"Rumble: upload {pct}%")
            last = pct
        if isinstance(pct, (int, float)) and pct >= 100:
            stable += 1
            if stable >= 2:        # seen 100% twice → genuinely done
                time.sleep(3)      # let Rumble register completion
                return True
        else:
            stable = 0
        time.sleep(2)
    log.warning(f"Rumble: upload did not reach 100% within {timeout}s (last={last}%)")
    return False


def _dbg(d, tag: str):
    """Diagnostic snapshot (screenshot + page-state dump) when RUMBLE_DEBUG_SHOTS
    is set. No-op in production. Helps see why publish doesn't yield a URL."""
    import os
    shot_dir = os.environ.get("RUMBLE_DEBUG_SHOTS")
    if not shot_dir:
        return
    try:
        p = Path(shot_dir)
        p.mkdir(parents=True, exist_ok=True)
        try:
            d.save_screenshot(str(p / f"{tag}.png"))
        except Exception:
            pass
        state = d.execute_script("""
        var out = {url: location.href, title: document.title};
        out.vlinks = Array.from(document.querySelectorAll('a[href*="rumble.com/v"]'))
            .slice(0,6).map(function(a){return (a.textContent||'').trim().slice(0,30)+' => '+a.href;});
        var dir = document.getElementById('direct');
        out.direct = dir ? (dir.value||dir.textContent||'').trim() : null;
        out.percents = (document.body.innerText.match(/\\d{1,3}\\s*%/g)||[]).slice(0,8);
        out.sf2_vis = (function(){var e=document.getElementById('submitForm2');return e?e.offsetParent!==null:false;})();
        out.body = (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,400);
        return out;
        """)
        log.info(f"RB[{tag}] {json.dumps(state)[:900]}")
    except Exception as e:
        log.info(f"RB[{tag}] dbg failed: {e}")


DEBUG_PORT = 9223
PROFILE_DIR = Path.home() / "Library/Application Support/FrameWiseChrome"
UPLOAD_URL = "https://rumble.com/upload.php"

# Maps category names to Rumble's internal data-value IDs
CATEGORY_VALUES = {
    "Automotive": "9",
    "Cooking": "2",
    "Entertainment": "15",
    "Finance & Crypto": "16",
    "Gaming": "4",
    "Health & Science": "6",
    "HowTo": "10",
    "Music": "12",
    "News": "9",
    "Podcasts": "20",
    "Sports": "5",
    "Technology": "8",
    "Travel": "13",
    "Viral": "23",
    "Vlogs": "21",
}


def _attach_chrome():
    """Attach Selenium to the running Chrome via the remote debugging port.
    Returns the WebDriver. Raises RuntimeError if Chrome isn't reachable."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        raise SystemExit("Run: pip3 install selenium")

    # Verify the debug endpoint is alive before opening a driver session
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read()
    except Exception as e:
        raise RuntimeError(
            f"Chrome not reachable on port {DEBUG_PORT}. "
            f"Run ensure_chrome.sh first. Error: {e}"
        )

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    d = webdriver.Chrome(options=opts)
    # 2026-07-01: without this, driver.get() uses the WebDriver default page_load_timeout
    # (300s). A stuck navigation then hangs for 24 min per retry (×3 = 72 min lost today).
    d.set_page_load_timeout(60)
    return d


def _switch_to_upload_tab(driver):
    """Find/create the upload.php tab and switch to it."""
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "upload.php" in driver.current_url:
            return True
    # No upload tab — open one in the first window
    driver.switch_to.window(driver.window_handles[0])
    driver.execute_script(f"window.open('{UPLOAD_URL}', '_blank');")
    time.sleep(2)
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "upload.php" in driver.current_url:
            return True
    return False


def _fresh_upload_tab(driver):
    """Force a VIRGIN upload tab: close all existing rumble tabs + open ONE fresh.
    Use this before every upload attempt — prevents stale-state failures from
    re-using a tab whose previous upload half-completed."""
    # Close every existing rumble.com tab
    to_close = []
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            if "rumble.com" in driver.current_url:
                to_close.append(handle)
        except Exception:
            continue
    for handle in to_close:
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            pass
    # Need at least one window to remain; if we closed everything, open a fresh tab
    if not driver.window_handles:
        # No tabs left — open via Chrome's debug API (PUT new tab)
        import urllib.request
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{DEBUG_PORT}/json/new?{UPLOAD_URL}", method='PUT'
            )
            urllib.request.urlopen(req, timeout=5).read()
            time.sleep(2)
        except Exception:
            pass
    # Switch to first remaining window and navigate to fresh upload page
    if driver.window_handles:
        driver.switch_to.window(driver.window_handles[0])
        # Open new tab via JS, then switch to it
        driver.execute_script(f"window.open('{UPLOAD_URL}', '_blank');")
        time.sleep(2)
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "rumble.com/upload" in driver.current_url:
                return True
    return False


def upload(video_path: Path, title: str, description: str, tags: List[str],
           cfg: dict, category: str = "Entertainment") -> str:
    """Upload a video to Rumble. Returns the published video URL.

    Requires Chrome to be running with --remote-debugging-port=9223 and
    user already logged into Rumble. Use ensure_chrome.sh to launch."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    d = _attach_chrome()

    try:
        # Use a FRESH upload tab every attempt (avoids stale state from previous fails)
        _fresh_upload_tab(d)
        time.sleep(6)
        # Belt: hard navigate to upload page in the new tab
        d.get(UPLOAD_URL)
        time.sleep(4)

        if "auth.rumble.com" in d.current_url or "login" in d.current_url:
            raise RuntimeError(
                "Chrome session not logged into Rumble. "
                "Open chrome on port 9223 and sign in manually first."
            )

        # 1. Send the video file
        log.info(f"Rumble: uploading file {video_path.name}")
        file_input = d.find_element(By.ID, "Filedata")
        file_input.send_keys(str(video_path.resolve()))
        time.sleep(3)
        _dbg(d, "01_after_file_send")

        # 2. Fill title/description/tags
        log.info("Rumble: filling metadata")
        # Strip non-BMP characters (emojis above U+FFFF) — ChromeDriver send_keys
        # crashes on them with "ChromeDriver only supports characters in the BMP"
        def _bmp_safe(s):
            return ''.join(ch for ch in s if ord(ch) < 0x10000)

        for field_id, value in [
            ("title", _bmp_safe(title)[:100]),
            ("description", _bmp_safe(description)[:2000]),
            ("tags", _bmp_safe(", ".join(tags[:10]) if isinstance(tags, list) else str(tags))),
        ]:
            el = d.find_element(By.ID, field_id)
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            el.clear()
            el.send_keys(value)

        # 3. Open category dropdown + click chosen option
        # AGGRESSIVE FIX (May 2026): the dropdown is fragile. Strategy:
        #   - Use CDP click (more trusted than Selenium native)
        #   - Verify the .select-options-container actually opens before polling for option
        #   - If not open after 3s, re-click up to 3 times
        #   - Once open, poll up to 60s for the option (Rumble's React lazy-renders the list)
        log.info(f"Rumble: selecting category={category!r}")

        def _find_primary_input():
            for s in d.find_elements(By.CSS_SELECTOR, "input.select-search-input"):
                if "primary" in (s.get_attribute("data-default-placeholder") or "").lower():
                    return s
            return None

        def _cdp_click_element(elem):
            """CDP-level mouse click on a Selenium WebElement at its viewport center."""
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.3)
            rect = d.execute_script("""
                var r = arguments[0].getBoundingClientRect();
                return {x: r.left + r.width/2, y: r.top + r.height/2};
            """, elem)
            for ev in ('mouseMoved', 'mousePressed', 'mouseReleased'):
                d.execute_cdp_cmd('Input.dispatchMouseEvent', {
                    'type': ev, 'x': rect['x'], 'y': rect['y'],
                    'button': 'left' if ev != 'mouseMoved' else 'none',
                    'clickCount': 1 if ev != 'mouseMoved' else 0,
                })

        def _dropdown_is_open():
            """Returns True if the options container is visible AND has options."""
            return d.execute_script("""
                var any = false;
                document.querySelectorAll('.select-options-container').forEach(function(el){
                    if(el.offsetParent !== null && el.querySelectorAll('.select-option').length > 0){
                        any = true;
                    }
                });
                return any;
            """)

        primary_input = _find_primary_input()
        if primary_input is None:
            raise RuntimeError("primary category dropdown input not found")

        # Try to OPEN the dropdown. 2026-06-09: bumped 3→6 attempts + longer waits —
        # "dropdown didn't open" was the #1 Rumble failure (UI lags under load).
        dropdown_open = False
        _MAXTRY = 6
        for open_attempt in range(_MAXTRY):
            _cdp_click_element(primary_input)
            time.sleep(4)
            if _dropdown_is_open():
                log.info(f"Rumble: dropdown opened on attempt {open_attempt+1}")
                dropdown_open = True
                break
            log.warning(f"Rumble: dropdown didn't open (attempt {open_attempt+1}/{_MAXTRY})")
            time.sleep(3)

        if not dropdown_open:
            raise RuntimeError(f"Rumble: category dropdown failed to open after {_MAXTRY} clicks")

        # Dropdown IS open — poll up to 60s for our specific option to render
        target = None
        for poll in range(60):
            target = d.execute_script("""
                var label = arguments[0];
                var found = null;
                document.querySelectorAll('.select-option').forEach(function(el){
                    if(found) return;
                    if(el.textContent.trim() === label && el.offsetParent !== null){
                        found = el;
                    }
                });
                return found;
            """, category)
            if target is not None:
                log.info(f"Rumble: {category!r} option appeared at +{poll}s")
                break
            time.sleep(1)
        if target is None:
            raise RuntimeError(f"category option {category!r} not in opened dropdown after 60s")

        d.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
        time.sleep(0.3)
        ActionChains(d).move_to_element(target).pause(0.2).click().perform()
        time.sleep(1)

        # 4. Tick ALL visible unchecked checkboxes (rights, terms, syndication options)
        log.info("Rumble: ticking all visible checkboxes")
        d.execute_script("""
            document.querySelectorAll('input[type=checkbox]').forEach(function(b){
                if(b.offsetParent !== null && !b.checked){
                    b.click();
                }
            });
        """)
        time.sleep(0.5)

        # 4.5 CRITICAL: wait for the file upload to fully finish (100%) before
        # advancing. Publishing an incomplete upload is silently ignored by Rumble
        # and leaves the run stuck on the form (the "no video URL found" failure).
        log.info("Rumble: waiting for file upload to reach 100% before publishing...")
        if not _wait_upload_complete(d):
            raise RuntimeError("Rumble: file upload never reached 100% — aborting before publish")

        # 5. Click step 1 → step 2 (#submitForm)
        log.info("Rumble: clicking submitForm (step 1)")
        d.execute_script("""
            var el = document.getElementById('submitForm');
            el.scrollIntoView({block:'center'});
            el.click();
            var $$ = window.jQuery || window.$;
            if($$) $$('#submitForm').trigger('click');
        """)

        # Wait for step 2 (submitForm2 becomes visible)
        log.info("Rumble: waiting for step 2...")
        step2_ok = False
        for i in range(120):
            time.sleep(1)
            visible = d.execute_script("""
                var sf2 = document.getElementById('submitForm2');
                return sf2 && sf2.offsetParent !== null;
            """)
            if visible:
                step2_ok = True
                break
            # 2026-06-09: re-click submitForm at ~45s in case the first click was lost
            # (file upload may not have hit 100% yet) — rescues "step 2 never appeared".
            if i == 45:
                log.warning("Rumble: step 2 not up after 45s — re-clicking submitForm")
                d.execute_script("var el=document.getElementById('submitForm'); if(el){el.click();}")
        if not step2_ok:
            raise RuntimeError("step 2 never appeared after submitForm click")

        # 6. Click step 2 license: "Rumble Only (non-exclusive)" — crcval=6.
        # As of late May 2026 Rumble added a license-selection gate. Without picking one,
        # submitForm2 silently doesn't advance. Click .greenLink[crcval=6] = Rumble Only.
        log.info("Rumble: selecting 'Rumble Only (non-exclusive)' license on step 2")
        time.sleep(1)
        d.execute_script("""
            var clicked = false;
            document.querySelectorAll('a.greenLink').forEach(function(el){
                if(clicked) return;
                if((el.getAttribute('crcval')||'') === '6'){
                    el.scrollIntoView({block:'center'});
                    el.click();
                    clicked = true;
                }
            });
        """)
        time.sleep(2)

        # Step-2 REQUIRED CHECKBOXES (root cause of "no video URL found" failures):
        # Rumble has TWO mandatory checkboxes that appear AFTER license selection:
        #   - #crights  "I have all rights to upload this content"
        #   - #cterms   "I agree to the Terms of Service"
        # The inputs are visually hidden — Rumble renders custom-styled labels for them.
        # A naive .click() on the input doesn't trigger form validation. We must:
        #   1. wait for them to be rendered (license-click reveals them)
        #   2. force checked=true on the input
        #   3. click the LABEL[for=...] so Rumble's JS state updates
        #   4. dispatch change + input events to trigger any handlers
        #   5. verify both are checked before clicking submit
        # Per user (2026-05-30): Rumble's server-side validation rejects JS-dispatched
        # checkbox clicks because they have `isTrusted: false`. After submit, Rumble shows
        # a "please confirm" warning + the checkboxes appear un-ticked again. We need a
        # TRUSTED click — Selenium's WebElement.click() generates a real browser MouseEvent
        # with isTrusted=true, which Rumble accepts.
        log.info("Rumble: ticking required step-2 checkboxes (TRUSTED click via Selenium)")
        time.sleep(3)  # let license-click reveal the checkboxes
        check_result = _tick_checkboxes_trusted(d)
        log.info(f"Rumble: checkbox state: {check_result}")
        time.sleep(1)
        if check_result.get('crights') == 'STILL_UNCHECKED' or check_result.get('cterms') == 'STILL_UNCHECKED':
            raise RuntimeError(f"required Rumble checkbox refused to tick: {check_result}")

        # 7. Click step 2 final submit (#submitForm2) — TRUSTED Selenium click first,
        #    fall back to JS multi-method. If a "please confirm" warning appears after
        #    submit, re-tick checkboxes via trusted click + re-submit. Up to 3 retries.
        _dbg(d, "02_before_publish")
        time.sleep(2)
        log.info("Rumble: clicking submitForm2 (final publish) — TRUSTED click first")

        max_retries = 3
        for submit_try in range(1, max_retries + 1):
            # Try TRUSTED Selenium click on submit button
            try:
                btn = d.find_element("css selector", "#submitForm2")
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.5)
                btn.click()
                log.info(f"  submit attempt {submit_try}: TRUSTED click OK")
            except Exception as e:
                log.info(f"  submit attempt {submit_try}: trusted click failed ({e.__class__.__name__}), falling back to JS")
                # Fallback: existing multi-method JS approach
                d.execute_script("""
                    var el = document.getElementById('submitForm2');
                    if(!el) return;
                    el.removeAttribute('disabled');
                    el.disabled = false;
                    el.classList.remove('disabled');
                    el.classList.remove('btn-disabled');
                    el.scrollIntoView({block: 'center'});
                    try { el.click(); } catch(e){}
                    ['mousedown','mouseup','click'].forEach(function(t){
                        try { el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); } catch(e){}
                    });
                    var $$ = window.jQuery || window.$;
                    if($$){ try { $$('#submitForm2').trigger('click'); } catch(e){} }
                    var form = el.closest('form');
                    if(form){ try { form.submit(); } catch(e){} }
                """)

            # Wait briefly for any warning to render
            time.sleep(2)
            warning = _detect_submit_warning(d)
            if warning:
                log.warning(f"  submit attempt {submit_try}: WARNING detected: {warning!r}")
                _dbg(d, f"warning_{submit_try}")
                # Re-tick checkboxes via trusted click + retry submit
                recheck = _tick_checkboxes_trusted(d)
                log.info(f"  re-ticked checkboxes: {recheck}")
                time.sleep(1)
                continue  # retry the submit
            # No warning → submit accepted, break out
            break
        else:
            log.error(f"Rumble: submit warning persisted after {max_retries} retries")

        # Final diagnostic snapshot of submit button state
        click_diag = d.execute_script("""
            var el = document.getElementById('submitForm2');
            return el ? {
                tag: el.tagName, disabled: !!el.disabled,
                visible: el.offsetParent !== null, classList: el.className,
            } : {error:'submitForm2 not found'};
        """)
        log.info(f"Rumble: post-submit button state: {click_diag}")
        _dbg(d, "03_after_publish_click")

        # 8. Wait for "VIDEO UPLOAD COMPLETE!" + extract REAL video URL
        #   Strategy A: <a> whose text starts with "View" — the just-uploaded video link
        #   Strategy B: <textarea id="direct"> holds the canonical Direct Link URL
        log.info("Rumble: waiting for upload-complete page...")
        video_url = None
        # Pre-compute a normalized title for channel-page matching (Strategy C)
        import re as _re
        title_norm = _re.sub(r'[^a-z0-9]+', '', (title or '').lower())[:40]

        for i in range(600):  # up to ~10 min (was 6) — Rumble encoding sometimes slow
            time.sleep(1)
            if i % 20 == 0:
                _dbg(d, f"04_wait_{i:03d}s")
            # Strategy A: "View" link to a rumble.com/v... URL on the success page
            video_url = d.execute_script("""
                var found = null;
                document.querySelectorAll('a[href*="rumble.com/v"]').forEach(function(el){
                    if(found) return;
                    var t = (el.textContent||'').trim();
                    if(t.indexOf('View') === 0 && el.offsetParent !== null){
                        found = el.href;
                    }
                });
                return found;
            """)
            if video_url:
                break
            # Strategy B: pull the canonical URL from Rumble's "Direct Link" textarea
            direct = d.execute_script("""
                var t = document.getElementById('direct');
                return t ? (t.value || t.textContent || '').trim() : null;
            """)
            if direct and direct.startswith("http"):
                video_url = direct
                break

            # Strategy C: if Rumble redirected to the user's channel page (NEW flow as of
            # late May 2026), poll the channel page for our just-uploaded video by title.
            current_url = d.execute_script("return location.href;") or ""
            if "/user/" in current_url and title_norm:
                channel_hit = d.execute_script("""
                    var titleNorm = arguments[0];
                    var found = null;
                    // Try all anchor + heading combos on the channel page
                    document.querySelectorAll('a[href*="rumble.com/v"], a[href^="/v"]').forEach(function(a){
                        if(found) return;
                        var t = ((a.textContent||'') + ' ' + (a.title||'') + ' ' +
                                 (a.querySelector('h1,h2,h3,h4') ? a.querySelector('h1,h2,h3,h4').textContent : '')).toLowerCase();
                        t = t.replace(/[^a-z0-9]+/g,'');
                        if(t.indexOf(titleNorm.substring(0,20)) !== -1){
                            var href = a.href;
                            if(href.indexOf('/v') !== -1) found = href;
                        }
                    });
                    return found;
                """, title_norm)
                if channel_hit:
                    video_url = channel_hit if channel_hit.startswith("http") else "https://rumble.com" + channel_hit
                    log.info(f"Rumble: Strategy C hit — found on channel page: {video_url}")
                    break

        if not video_url:
            _dbg(d, "05_failed_final")
            # On final fail, log what URL we ended up on for diagnostic
            try:
                final_url = d.execute_script("return location.href;")
                log.error(f"Rumble final URL: {final_url}")
            except Exception:
                pass
            raise RuntimeError("upload completed but no video URL found on page")

        log.info(f"Rumble ✅  {video_url}")

        # Refresh saved cookies for next run
        try:
            cookie_path = Path.home() / "RedditReels/config/rumble_cookies.json"
            cookie_path.write_text(json.dumps(d.get_cookies(), indent=2))
        except Exception:
            pass

        return video_url

    finally:
        # IMPORTANT: do NOT quit the driver — that would kill the user's Chrome.
        # Just disconnect.
        pass


if __name__ == "__main__":
    import argparse, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--upload", required=True, help="path to video")
    p.add_argument("--title", default="FrameWise Cinema test")
    p.add_argument("--description", default="Test upload via attached Chrome.")
    p.add_argument("--tags", default="motivation,quotes")
    p.add_argument("--category", default="Entertainment")
    args = p.parse_args()
    cfg_path = Path.home() / "RedditReels/config/credentials.json"
    cfg = json.loads(cfg_path.read_text())
    url = upload(
        Path(args.upload), args.title, args.description,
        args.tags.split(","), cfg, args.category,
    )
    print(f"\nDONE: {url}")
