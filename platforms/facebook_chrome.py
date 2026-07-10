"""
Facebook Reels uploader for FrameWise Cinema Page — via attached Chrome.

Architecture (same as platforms/rumble_chrome.py):
  - Connects to the persistent Chrome instance running on port 9223
    (launched by ensure_chrome.sh, profile at ~/Library/Application Support/FrameWiseChrome)
  - User must be logged into FB in that Chrome (cookies persist across runs)
  - Uses Chrome DevTools Protocol (CDP) Input.dispatchMouseEvent to fire
    real-browser-level mouse events — FB's React UI accepts these as real clicks
    where synthetic Selenium clicks fail

PUBLISH FLOW (4 stages discovered via reverse engineering):
  Stage 1: Page profile → click 'Reel' button → modal opens
  Stage 2: Drop file → 'Next' (advances to Edit Reel step)
  Stage 3: 'Next' (advances from Edit Reel → audience/scheduling step)
  Stage 4: 'Post' button publishes the Reel
"""

import json, logging, subprocess, time
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)

DEBUG_PORT = 9223
PAGE_ID_DEFAULT = "61590613942018"  # FrameWise Cinema FB Page ID


def _attach_chrome():
    """Attach Selenium to running Chrome via remote-debugging-port."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    import urllib.request

    # Verify port is alive
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read()
    except Exception as e:
        raise RuntimeError(f"Chrome not reachable on port {DEBUG_PORT}. Run ensure_chrome.sh first. {e}")

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    d = webdriver.Chrome(options=opts)
    # Same fix as rumble_chrome: without a timeout the default is 300s, so a slow
    # FB page load (reels tab or composer) can hang the poll for minutes per attempt.
    d.set_page_load_timeout(60)
    return d


def _ensure_fb_tab(driver):
    """Force a VIRGIN Facebook tab: close ALL existing fb.com tabs + open ONE fresh.
    Prevents stale-state failures from re-using a tab whose previous upload half-completed."""
    PAGE_URL = "https://www.facebook.com/profile.php?id=61590613942018"

    # Close every existing facebook.com tab (they may have stale composer state)
    to_close = []
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            if "facebook.com" in driver.current_url:
                to_close.append(h)
        except Exception:
            continue
    for h in to_close:
        try:
            driver.switch_to.window(h)
            driver.close()
        except Exception:
            pass

    # Open a fresh FB tab via Chrome debug API (PUT)
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{DEBUG_PORT}/json/new?{PAGE_URL}",
            method='PUT',
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        # Fall back: open via JS in remaining window
        try:
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
                driver.execute_script(f"window.open('{PAGE_URL}', '_blank');")
        except Exception:
            pass

    # Poll up to 20s for the fresh FB tab to appear + load
    for _ in range(20):
        time.sleep(1)
        for h in driver.window_handles:
            try:
                driver.switch_to.window(h)
                if "facebook.com" in driver.current_url:
                    return True
            except Exception:
                continue
    return False


def _fb_try_add_trending_music(d, log, cdp_click):
    # Tee log.info to print() too so output shows in claude_upload's stdout capture
    # (claude_upload uses print() not logging — pure log.info goes to a separate handler)
    _orig_info = log.info
    def _info(msg):
        print(f"  {msg}")
        try: _orig_info(msg)
        except Exception: pass
    log.info = _info
    """Best-effort: open FB Reels music picker → select first trending track → apply.
    Falls back silently if music picker unavailable (some Page accounts don't have access).

    `cdp_click` must be passed in (it's a closure inside upload() — defined there because
    it captures the websocket connection).

    Strategy:
      1. Find any button labeled "Add audio" / "Add music" / "Music" / aria-label="Audio"
      2. Click it → wait for music library panel to render
      3. Find first track-like card in the panel + click "Use" / track itself
      4. Confirm music was applied (look for waveform / "Selected" indicator)
      5. Click Done / Apply to return to Edit Reel
    """
    import time as _t

    # Step 1: find an audio-add button
    log.info("FB: searching for 'Add audio' button on Edit Reel step")
    audio_btn_id = d.execute_script("""
        var keywords = ['add audio','audio','music','add music','sound'];
        var found = null;
        // First try aria-label match
        document.querySelectorAll('[aria-label], [role=button]').forEach(function(el){
            if(found || el.offsetParent === null) return;
            var lbl = ((el.getAttribute('aria-label')||'') + ' ' + (el.innerText||'')).toLowerCase().trim();
            for(var k of keywords){
                if(lbl === k || lbl.startsWith(k+' ') || lbl.endsWith(' '+k) || lbl.indexOf(' '+k+' ')!==-1){
                    if(!el.id) el.id = 'fb_audio_btn_'+Math.random().toString(36).substring(7);
                    found = el.id;
                    return;
                }
            }
        });
        return found;
    """)
    if not audio_btn_id:
        log.info("FB: no 'Add audio' button — Page Reels music picker not available, publishing silent")
        return False

    log.info(f"FB: clicking audio button {audio_btn_id!r}")
    if not cdp_click(audio_btn_id):
        log.info("FB: audio button click failed, publishing silent")
        return False
    _t.sleep(4)

    # Step 2: wait for music library panel + find first track item
    log.info("FB: looking for first music track in library panel")
    track_id = None
    for retry in range(8):
        track_id = d.execute_script("""
            var found = null;
            // Track items are usually buttons or rows with audio-related attributes
            document.querySelectorAll('[role=button], [role=option], div[tabindex]').forEach(function(el){
                if(found || el.offsetParent === null) return;
                var lbl = (el.getAttribute('aria-label')||'').toLowerCase();
                var text = (el.innerText||'').toLowerCase();
                // Skip already-clicked buttons + obvious non-track items
                if(lbl.indexOf('close')!==-1 || lbl.indexOf('back')!==-1) return;
                if(text.indexOf('search')!==-1 && text.length < 20) return;
                // Track items typically have artist + duration text
                var hasDuration = /\\d+:\\d{2}/.test(text);
                var hasMusicIcon = el.querySelector('svg, img');
                if((hasDuration && hasMusicIcon) || lbl.indexOf('preview')!==-1 || lbl.indexOf('play track')!==-1){
                    if(!el.id) el.id = 'fb_track_'+Math.random().toString(36).substring(7);
                    found = el.id;
                }
            });
            return found;
        """)
        if track_id:
            break
        _t.sleep(1)

    if not track_id:
        log.info("FB: no track items found in music panel, publishing silent")
        return False

    log.info(f"FB: clicking first track {track_id!r}")
    if not cdp_click(track_id):
        log.info("FB: track click failed, publishing silent")
        return False
    _t.sleep(3)

    # Step 3: confirm + apply (look for "Use this song" / "Done" / "Apply")
    confirm_id = d.execute_script("""
        var labels = ['use this song','use song','apply','done','select','add'];
        var found = null;
        document.querySelectorAll('[role=button], button').forEach(function(el){
            if(found || el.offsetParent === null) return;
            var t = ((el.getAttribute('aria-label')||'') + ' ' + (el.innerText||'')).toLowerCase().trim();
            for(var l of labels){
                if(t === l || t.startsWith(l)){
                    if(!el.id) el.id = 'fb_apply_'+Math.random().toString(36).substring(7);
                    found = el.id;
                    return;
                }
            }
        });
        return found;
    """)
    if confirm_id:
        # Scroll the apply button into viewport — it's often below the fold
        try:
            d.execute_script(
                "var e=document.getElementById(arguments[0]); if(e) e.scrollIntoView({block:'center', inline:'center'});",
                confirm_id
            )
            _t.sleep(1)
        except Exception: pass
        log.info(f"FB: clicking music-apply button {confirm_id!r}")
        clicked = cdp_click(confirm_id)
        if not clicked:
            # CDP click rejected (likely still off-viewport on a hidden parent) — fall back to JS click
            log.info("  cdp_click rejected, JS-clicking apply button as fallback")
            try:
                d.execute_script("""
                    var e=document.getElementById(arguments[0]);
                    if(e){
                        e.click();
                        ['mousedown','mouseup','click'].forEach(function(t){
                            e.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}));
                        });
                    }
                """, confirm_id)
            except Exception as e:
                log.info(f"  JS click also failed: {e}")
        _t.sleep(2)
    else:
        log.info("FB: no explicit apply button — music may be auto-applied on track click")

    log.info("FB: ✓ trending music applied (best-effort)")
    return True


def upload(video_path: Path, title: str, description: str, tags: List[str], cfg: dict) -> str:
    """Post a Reel to FrameWise Cinema page. Returns the public Reel URL.

    HYBRID MUSIC MODE (2026-05-30): if cfg.get('fb_use_trending_music', True):
      - Strips audio from the video before upload (silent version)
      - During Edit Reel step, attempts to click 'Add Audio' and pick a trending track
        from FB's licensed music library (Meta deal with labels covers Page Reels too)
      - Falls back silently if music library unavailable (publishes silent)

    Args:
        video_path: local path to the rendered .mp4
        title:       short title (not used by FB Reels — kept for API symmetry with YT/Rumble)
        description: caption text (will be posted to the Reel; FB strips/trims as needed)
        tags:        list of hashtags (currently bundled into description by upstream caller)
        cfg:         credentials.json contents (provides facebook_page_id + fb_use_trending_music)

    Returns the public Reel URL on success; raises RuntimeError on failure."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    # HYBRID MUSIC MODE: strip audio so FB's licensed library track is the only audio.
    # OFF by default — only enable per-pipeline via cfg.fb_use_trending_music=True.
    # ON for FrameWise (cinematic mood, no narration), OFF for RR/SS (would kill voice).
    fb_use_trending = cfg.get("fb_use_trending_music", False)
    if fb_use_trending:
        try:
            import subprocess as _sp
            silent_path = video_path.with_suffix(".fb_silent.mp4")
            _sp.run(["ffmpeg","-y","-loglevel","error","-i", str(video_path),
                     "-c:v","copy","-an", str(silent_path)], check=True)
            log.info(f"FB: stripped audio for trending-music mode → {silent_path.name}")
            video_path = silent_path
        except Exception as e:
            log.warning(f"FB: audio strip failed ({e}), uploading with original audio")

    page_id = cfg.get("facebook_page_id", PAGE_ID_DEFAULT)
    page_url = f"https://www.facebook.com/profile.php?id={page_id}"

    # NOTE: do NOT activate Chrome. CDP Input.* events work without foreground focus,
    # so the user can keep working in their current app while this runs.
    # Pre-publish stages (attach → tab → snapshot → open composer) are SAFE to retry:
    # no reel is posted until the final Share click much later. A contended/closed
    # browser here ("invalid session id: browser has closed the connection") is
    # recovered by re-attaching a fresh Selenium session. If recovery ultimately
    # fails, the error is marked "FB_PREPUBLISH:" so the caller knows nothing was
    # posted and a later fire may retry without risking a duplicate.
    _INFRA_ERRS = ("invalid session id", "session deleted", "browser has closed",
                   "no such window", "disconnected", "target window already closed",
                   "web view not found", "not reachable on port", "chrome not reachable")
    def _is_infra_err(exc) -> bool:
        return any(k in str(exc).lower() for k in _INFRA_ERRS)
    def _attach_with_tab():
        _d = _attach_chrome()
        if not _ensure_fb_tab(_d):
            raise RuntimeError("could not switch to a facebook.com tab")
        return _d

    d = _attach_with_tab()

    # ============================================================
    # DUPLICATE PREVENTION:
    # Snapshot the Reels tab BEFORE attempting upload. If a NEW reel
    # appears after publish, that's our success URL — even if our
    # script's intermediate detection said "fail" and would have retried.
    # This stops the "retry posts duplicates" problem.
    # ============================================================
    def _snapshot_reel_ids():
        """Return the set of reel IDs currently on the Page's Reels tab."""
        try:
            d.get(f"https://www.facebook.com/profile.php?id={page_id}&sk=reels_tab")
            time.sleep(6)
            ids = d.execute_script("""
            var out = new Set();
            document.querySelectorAll('a[href*="/reel/"]').forEach(function(el){
                var m = (el.href||'').match(/\\/reel\\/(\\d+)/);
                if(m) out.add(m[1]);
            });
            return Array.from(out);
            """)
            return set(ids or [])
        except Exception as e:
            log.warning(f"FB: snapshot failed: {e}")
            return set()

    log.info("FB: snapshotting existing reels before upload (duplicate-prevention)")
    pre_ids = _snapshot_reel_ids()
    log.info(f"FB: {len(pre_ids)} reels already on Page before this upload")

    # ---- helpers wired to driver ----

    def cdp_click(elem_id):
        """Fire a real Chrome mouse-click at the element's viewport center.
        Returns False if element is missing or off the viewport."""
        rect = d.execute_script("""
        var el = document.getElementById(arguments[0]);
        if(!el) return null;
        var r = el.getBoundingClientRect();
        return {x: r.left + r.width/2, y: r.top + r.height/2};
        """, elem_id)
        if not rect:
            return False
        vp = d.execute_script("return {w: window.innerWidth, h: window.innerHeight};")
        if rect['x'] < 0 or rect['y'] < 0 or rect['x'] > vp['w'] or rect['y'] > vp['h']:
            log.warning(f"FB: element {elem_id} off-viewport ({rect['x']:.0f},{rect['y']:.0f})")
            return False
        for ev in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            d.execute_cdp_cmd('Input.dispatchMouseEvent', {
                'type': ev, 'x': rect['x'], 'y': rect['y'],
                'button': 'left' if ev != 'mouseMoved' else 'none',
                'clickCount': 1 if ev != 'mouseMoved' else 0,
            })
        return True

    def find_visible_button(text_match):
        """Find the best-matching clickable button and SCROLL IT INTO VIEW.

        2026-06-30 robustness fix (was the cause of '52 wizard stuck'): the old version
        required an EXACT text match AND the button to already sit inside the viewport, so a
        renamed FB button ('Share now' vs 'post') or one below the dialog fold was invisible
        to it. Now: score exact > startswith > short-contains, include a[role=button], and
        scrollIntoView the winner so the viewport is never the blocker.
        """
        return d.execute_script("""
        var match = arguments[0].toLowerCase();
        var best = null, bestScore = -1;
        document.querySelectorAll('div[role=button], button, a[role=button]').forEach(function(el){
            if(el.offsetParent === null) return;
            if(el.getAttribute('aria-disabled') === 'true' || el.disabled) return;
            var t = (el.textContent||'').trim().toLowerCase();
            if(!t) return;
            var score = -1;
            if(t === match) score = 3;
            else if(t.indexOf(match) === 0) score = 2;                         // startsWith
            else if(t.indexOf(match) !== -1 && t.length <= match.length + 12) score = 1;  // short contains
            if(score > bestScore){
                if(!el.id) el.id = 'btn_'+Math.random().toString(36).substring(7);
                best = el.id; bestScore = score;
            }
        });
        if(best){ var e = document.getElementById(best); if(e){ try{ e.scrollIntoView({block:'center'}); }catch(_){} } }
        return best;
        """, text_match)

    # ---- Stage 1: navigate to Page + click Reel ----
    # 2026-06-13 FIX: FB REMOVED the "Create Reel" button from the Page profile (only a
    # "Reels" view-tab remains), which broke the old button-hunt ("Reel button not found").
    # Navigate DIRECTLY to the reel composer URL — it reliably opens the dialog with the
    # video file input ready (verified: dialog + input[type=file] + "Add Video" present).
    log.info("FB: opening reel composer (facebook.com/reels/create/)")
    for _open_try in range(2):
        try:
            d.get("https://www.facebook.com/reels/create/")
            time.sleep(9)
            break
        except Exception as e:
            # Still pre-publish — re-attach a fresh session once, then retry.
            if _open_try == 0 and _is_infra_err(e):
                log.warning(f"FB: browser session died before publish — re-attaching ({e})")
                try:
                    d = _attach_with_tab()
                    pre_ids = _snapshot_reel_ids()  # refresh on the new session
                except Exception as e2:
                    raise RuntimeError(f"FB_PREPUBLISH: re-attach failed: {e2}")
                continue
            raise RuntimeError(f"FB_PREPUBLISH: composer open failed: {e}")
    if "login" in d.current_url or "checkpoint" in d.current_url:
        raise RuntimeError(f"FB_PREPUBLISH: reel composer redirected to {d.current_url} — not logged in / checkpoint")

    # ---- Stage 2: send video file ----
    log.info(f"FB: sending video {video_path.name}")
    fid = d.execute_script("""
    var found = null, firstFile = null;
    document.querySelectorAll('input[type=file]').forEach(function(el){
        if(!firstFile){ if(!el.id) el.id='fb_file_in'; firstFile = el.id; }
        if(found) return;
        if((el.getAttribute('accept')||'').indexOf('video') !== -1){
            if(!el.id) el.id = 'fb_file_in';
            found = el.id;
        }
    });
    return found || firstFile;   // prefer video-accept input, else first file input
    """)
    if not fid:
        raise RuntimeError("FB: video file input not found in reel composer")
    from selenium.webdriver.common.by import By
    d.find_element(By.ID, fid).send_keys(str(video_path.resolve()))
    log.info("FB: waiting 30s for upload to process")
    time.sleep(30)

    # ---- Stage 2.5: advance to Edit Reel step, then fill caption ----
    # The caption (contenteditable div) lives on the Edit Reel step which appears
    # after the FIRST Next click. We use CDP Input.insertText to type the caption
    # without requiring Chrome to be in the foreground (no osascript keystrokes).

    # First Next: upload step → Edit Reel step
    log.info("FB: clicking Next to advance to Edit Reel step")
    for retry in range(3):
        nid = find_visible_button('next')
        if nid and cdp_click(nid):
            break
        time.sleep(2)
    time.sleep(5)

    # MUSIC PICKER (hybrid mode) — try to add trending music from FB's licensed library.
    # Best-effort: if the music picker isn't available on Page Reels, log + continue silent.
    if fb_use_trending:
        try:
            _fb_try_add_trending_music(d, log, cdp_click)
        except Exception as e:
            log.warning(f"FB: music picker failed (non-blocking, publishing silent): {e}")

    # Find caption field on the Edit Reel step.
    # FB renders TWO contenteditables in the dialog — one at x=-212 (off-viewport,
    # hidden sub-panel) and one at x=277 (the actual visible caption). Layout varies
    # between runs. ALWAYS pick the IN-VIEWPORT one with w>=100 and h>=15.
    log.info("FB: locating caption field (in-viewport contenteditable)")
    cap_id = None
    for retry in range(15):
        all_caps = d.execute_script("""
        var out = [];
        document.querySelectorAll('div[role=dialog] div[contenteditable=true]').forEach(function(el){
            if(el.offsetParent === null) return;
            var r = el.getBoundingClientRect();
            var cx = r.left + r.width/2, cy = r.top + r.height/2;
            var in_vp = (cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight);
            if(!el.id) el.id = 'fb_cap_'+Math.random().toString(36).substring(7);
            out.push({id: el.id, w: Math.round(r.width), h: Math.round(r.height), in_viewport: in_vp});
        });
        return out;
        """)
        # Prefer in-viewport contenteditable with reasonable size
        chosen = next((c for c in all_caps
                       if c['in_viewport'] and c['w'] >= 100 and c['h'] >= 15), None)
        if chosen:
            cap_id = chosen['id']
            log.info(f"FB: caption field found on attempt {retry+1}: {chosen}")
            break
        time.sleep(1)

    if cap_id:
        # Build caption text: description + hashtags
        full_caption = description.strip()
        if tags:
            tag_str = " ".join("#" + t.strip().replace(" ", "").replace(",", "")
                               for t in tags if t.strip())
            if tag_str and "#" not in full_caption:
                full_caption = full_caption + " " + tag_str

        # FB's Reel caption uses Lexical editor. 2026-06-04 FIX: the old osascript
        # Cmd+V clipboard paste landed EMPTY every time (Chrome focus/clipboard race —
        # FB caption was blank on every reel for days). Replaced with CDP
        # Input.insertText (a trusted browser-level text event Lexical DOES accept),
        # with selenium send_keys + clipboard paste as fallbacks. Verifies + retries.
        def _verify():
            return d.execute_script(
                "var el=document.getElementById(arguments[0]);"
                "return el?el.textContent.trim():'';", cap_id) or ""

        landed = ""
        # --- Method 1: CDP Input.insertText (most reliable for Lexical) ---
        try:
            log.info(f"FB: caption ({len(full_caption)} chars) → CDP Input.insertText")
            try:
                el = d.find_element(By.ID, cap_id); el.click()
            except Exception:
                cdp_click(cap_id)
            time.sleep(0.6)
            d.execute_script("document.getElementById(arguments[0]).focus();", cap_id)
            time.sleep(0.3)
            d.execute_cdp_cmd("Input.insertText", {"text": full_caption})
            time.sleep(2)
            landed = _verify()
        except Exception as e:
            log.warning(f"FB: CDP insertText failed: {e}")

        # --- Method 2: selenium send_keys into the focused element ---
        if len(landed) < 5:
            try:
                log.info("FB: caption empty after CDP → trying send_keys")
                el = d.find_element(By.ID, cap_id); el.click(); time.sleep(0.4)
                el.send_keys(full_caption)
                time.sleep(2)
                landed = _verify()
            except Exception as e:
                log.warning(f"FB: send_keys failed: {e}")

        # --- Method 3: clipboard Cmd+V (legacy fallback) ---
        if len(landed) < 5:
            try:
                log.info("FB: caption still empty → clipboard Cmd+V fallback")
                subprocess.run(['pbcopy'], input=full_caption.encode(), check=True)
                subprocess.run(['osascript','-e','tell application "Google Chrome" to activate'],
                               capture_output=True)
                time.sleep(1.2)
                cdp_click(cap_id); time.sleep(0.5)
                d.execute_script("document.getElementById(arguments[0]).focus();", cap_id)
                time.sleep(0.3)
                subprocess.run(['osascript','-e',
                    'tell application "System Events" to keystroke "v" using {command down}'],
                    capture_output=True)
                time.sleep(2.5)
                landed = _verify()
            except Exception as e:
                log.warning(f"FB: clipboard fallback failed: {e}")

        log.info(f"FB: caption field now contains: {landed[:80]!r} ({len(landed)} chars)")
    else:
        log.warning("FB: caption field not found after 15 retries — posting without caption")

    # ---- Stage 3-5: navigate remaining wizard to Publish ----
    posted = False
    prev_state = None
    # True only after we click a real Post/Publish/Share button. Gates the "Posting"
    # spinner check below: the caption text (the full Reddit story) and FB's own
    # Edit-Reel UI can contain the word "posting"/"publishing", which used to
    # false-trigger the spinner branch at step 0 — skipping the actual Publish click
    # and leaving the reel unposted (observed 2026-06-15: full flow ran, NEW=0).
    terminal_clicked = False
    for step in range(12):
        # Detect terminal state
        in_dialog = d.execute_script("return !!document.querySelector('div[role=dialog]');")
        if not in_dialog:
            log.info(f"FB: dialog closed after step {step} — assuming post sent")
            posted = True
            break

        # DIAGNOSTIC: log every button currently in the dialog (text + viewport + enabled)
        # so we can see exactly what FB renders at the step where the wizard got stuck.
        try:
            btn_dump = d.execute_script("""
            var out = [];
            var vw = window.innerWidth, vh = window.innerHeight;
            document.querySelectorAll('div[role=dialog] div[role=button], div[role=dialog] button').forEach(function(el){
                var t = (el.textContent||'').trim().substring(0,40);
                if(!t) return;
                var r = el.getBoundingClientRect();
                var cx = r.left + r.width/2, cy = r.top + r.height/2;
                var in_vp = (cx>=0 && cy>=0 && cx<=vw && cy<=vh);
                out.push({t:t, vp:in_vp, vis:(el.offsetParent!==null),
                          dis:el.getAttribute('aria-disabled')==='true'});
            });
            return out;
            """)
            log.info(f"FB[step {step}]: dialog buttons = {btn_dump}")
        except Exception as _e:
            log.info(f"FB[step {step}]: button dump failed: {_e}")

        # Optional screenshot diagnostic (only when FB_DEBUG_SHOTS is set, so
        # production cron runs never write these). Lets us SEE the composer at
        # each step to understand why the post is being discarded.
        import os as _os
        if _os.environ.get("FB_DEBUG_SHOTS"):
            try:
                shot_dir = Path(_os.environ["FB_DEBUG_SHOTS"])
                shot_dir.mkdir(parents=True, exist_ok=True)
                d.save_screenshot(str(shot_dir / f"step_{step:02d}.png"))
            except Exception as _se:
                log.info(f"FB[step {step}]: screenshot failed: {_se}")

        # --- "Posting" in progress? -------------------------------------------
        # After Post → "Not now", FB shows a "Posting" spinner with NO actionable
        # buttons while it finalizes the Reel. This is SUCCESS-in-progress, NOT a
        # stuck wizard. Detect it by the dialog's text (NOT a progressbar — the
        # video scrubber is a progressbar and would false-positive on earlier
        # steps). Wait for the dialog to close, then treat as posted. Checked
        # before the terminal-button block so we never re-click Post mid-posting.
        posting_now = d.execute_script("""
        var dlg = document.querySelector('div[role=dialog]');
        if(!dlg) return false;
        var t = (dlg.innerText||'').toLowerCase();
        return t.indexOf('posting') !== -1 || t.indexOf('publishing') !== -1;
        """)
        if posting_now and terminal_clicked:
            log.info("FB: 'Posting' spinner detected — waiting for it to finish")
            for _ in range(24):  # up to ~72s
                time.sleep(3)
                # 2026-06-07 FIX (the ~50% FB failure): an "Add a button to your reel?"
                # interstitial ("Not now"/"Skip") often appears WHILE the dialog text
                # still contains "posting" — which sent us down this spinner-wait branch
                # instead of dismissing it. The reel does NOT finalize until dismissed,
                # so we'd wait 72s, time out, falsely set posted=True, and no reel appears.
                # Dismiss the interstitial inside the wait loop so the post actually lands.
                for _dz in ('not now', 'skip', 'skip for now', 'maybe later'):
                    _did = find_visible_button(_dz)
                    if _did and cdp_click(_did):
                        log.info(f"FB: dismissed interstitial via {_dz!r} during posting-wait")
                        time.sleep(4)
                        break
                if not d.execute_script("return !!document.querySelector('div[role=dialog]');"):
                    break
            posted = True
            break

        # --- Dismiss upsell/interstitial FIRST ---------------------------------
        # After clicking "Post", FB pops an "Add a WhatsApp button" (and similar)
        # upsell interstitial OVER the composer. The Reel does NOT finalize until
        # this is dismissed. The dismiss control is "Not now" / "Skip" — these
        # texts only appear on such upsells, so clicking them is the correct
        # "skip enhancement, keep my post" action. We MUST do this before trying
        # a terminal button again (otherwise we re-click Post and the post resets).
        dismissed = False
        for dismiss in ('not now', 'skip', 'skip for now', 'maybe later'):
            did = find_visible_button(dismiss)
            if did and cdp_click(did):
                log.info(f"FB: dismissed interstitial via {dismiss!r}")
                dismissed = True
                time.sleep(6)
                break
        if dismissed:
            # Re-evaluate: the post should now be finalizing / dialog closing.
            if not d.execute_script("return !!document.querySelector('div[role=dialog]');"):
                posted = True
                break
            continue

        # Try Post / Publish / Share Now next
        terminal_btn = None
        for label in ('post', 'publish', 'share now', 'share'):
            bid = find_visible_button(label)
            if bid:
                terminal_btn = (label, bid)
                break

        if terminal_btn:
            label, bid = terminal_btn
            log.info(f"FB: clicking terminal button {label!r}")
            if cdp_click(bid):
                terminal_clicked = True
                time.sleep(10)
                # Detect post-completion: dialog closes or success indicator
                if not d.execute_script("return !!document.querySelector('div[role=dialog]');"):
                    posted = True
                    break
                # else continue — a follow-up upsell ("Not now") is handled at the
                # top of the next iteration before we'd ever re-click Post.
                continue

        # Otherwise click Next (visible only — filters out the offscreen duplicate)
        nid = find_visible_button('next')
        if not nid:
            log.warning("FB: no visible Next or Publish button — wizard stuck")
            break
        if not cdp_click(nid):
            log.warning("FB: Next click failed")
            break
        time.sleep(5)

        # State-change watchdog (avoid infinite Next loop on same step)
        snapshot = d.execute_script("""
        var btns = [];
        document.querySelectorAll('div[role=dialog] div[role=button], div[role=dialog] button').forEach(function(el){
            if(el.offsetParent !== null){
                var t = (el.textContent||'').trim().substring(0,30);
                if(t) btns.push(t);
            }
        });
        return btns.sort().join('|');
        """)
        if snapshot == prev_state:
            log.warning("FB: state unchanged across cycles — bailing wizard loop")
            break
        prev_state = snapshot

    # ============================================================
    # DUPLICATE-AWARE VERIFICATION (replaces the old `if not posted: raise`):
    # FB sometimes "silently posts" — our wizard detection said fail, but
    # the Reel actually went up. Check the Reels tab and diff against the
    # pre-snapshot. If a NEW reel ID appeared, that's our success.
    # If not, it's a real failure.
    # ============================================================
    log.info("FB: post-upload check — looking for NEW reel ID vs pre-snapshot")
    # A freshly-posted Reel takes time to process and appear on the Reels tab
    # (observed: tens of seconds). Poll several times before concluding failure,
    # otherwise we get a FALSE negative on a post that actually went up.
    new_ids = set()
    post_ids = set()
    for attempt in range(8):  # ~8 * 15s = up to ~120s
        time.sleep(15)
        post_ids = _snapshot_reel_ids()
        new_ids = post_ids - pre_ids
        log.info(f"FB: check {attempt+1}/8 — pre={len(pre_ids)} post={len(post_ids)} NEW={len(new_ids)}")
        if new_ids:
            break

    if not new_ids:
        # No new reel after polling — the upload truly failed
        raise RuntimeError(
            f"FB: no new reel appeared on Reels tab after polling (pre={len(pre_ids)}, "
            f"post={len(post_ids)}) — upload truly failed, NOT retrying to avoid duplicates"
        )

    if len(new_ids) > 1:
        log.warning(f"FB: multiple new reels detected ({new_ids}) — taking the first; "
                    f"manual cleanup may be needed")

    new_id = sorted(new_ids)[0]
    reel_url = f"https://www.facebook.com/reel/{new_id}/"
    log.info(f"FB ✅  {reel_url}  (id={new_id})")
    return reel_url


if __name__ == "__main__":
    import argparse, sys, json as _json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--upload", required=True, help="path to video")
    p.add_argument("--description", default="Test caption for FrameWise Cinema #Shorts")
    args = p.parse_args()
    cfg_path = Path.home() / "RedditReels/config/credentials.json"
    cfg = _json.loads(cfg_path.read_text())
    url = upload(Path(args.upload), title="", description=args.description, tags=[], cfg=cfg)
    print(f"\nDONE: {url}")
