#!/usr/bin/env python3
"""Compress Reddit post into open-loop hook + 45s narration via Claude haiku."""
import json, os, pathlib, sys
import sys as _lsys, pathlib as _lpath; _lsys.path.insert(0, str(_lpath.Path(__file__).resolve().parents[1])); from llm import Anthropic

import os, pathlib
ROOT = pathlib.Path(os.environ.get('RR_ROOT', os.path.expanduser('~/RedditReels')))
WORK = pathlib.Path(os.environ.get('RR_WORK', str(ROOT / 'output')))
WORK.mkdir(parents=True, exist_ok=True)

CREDS = json.load(open(os.path.expanduser("~/RedditReels/config/credentials.json")))
client = Anthropic(api_key=CREDS.get("anthropic_api_key", ""))

# SIGNATURE SERIES (2026-06-25, task C — "Am I The Villain?"): give the channel ONE identity
# instead of random AITA. Config-driven but DEFAULTS to the approved live values so it ships
# even without config keys (charter forbids editing credentials.json). Set series_enabled:false
# in creds to disable, or override series_spoken_hook / series_title_prefix to retune.
SERIES_ENABLED      = bool(CREDS.get("series_enabled", True))
SERIES_SPOKEN_HOOK  = (CREDS.get("series_spoken_hook")  or "You decide — am I the villain?").strip()
SERIES_TITLE_PREFIX = CREDS.get("series_title_prefix", "Villain? ")

IN  = pathlib.Path((WORK / "story.json"))
OUT = pathlib.Path((WORK / "script.json"))

import random as _random
# DURATION VARIANT (2026-06-25, task A — chase FB's +63%-views-for-<30s lever):
# Bias the shared render toward the SHORT variant (0.6, was 0.5) so MORE fires land under
# 30s, and target ~24s (was 21) so the "rapid" reel sits squarely in FB's 22-28s sweet spot.
# This is the SIMPLE option Vijaxx approved: NO upload/cadence change and NO fire-skipping —
# every fire still posts to FB exactly as before; only the LENGTH distribution shifts shorter.
# RR_DURATION_S env var forces a specific length for a given fire.
_rapid = _random.random() < 0.6
TARGET_DURATION_S = int(os.environ.get("RR_DURATION_S") or (26 if _rapid else 45))
_is_short = TARGET_DURATION_S <= 30
# 2026-06-30 ROOT-CAUSE FIX: the old (48,60) budget rendered to ~15-18s at the measured TTS
# rate (avg 2.88, up to 3.47 words/sec), so ~8% of fires produced <20s reels that the
# pre-upload sanity gate ABORTED — a wasted fire (no post anywhere). 72 words clears 20s even
# at the fastest rate (72/3.47≈20.7s) and stays <30s at the average (≈25s) for FB's lever.
WORD_BUDGET = (72, 82) if _is_short else (95, 140)
DURATION_VARIANT = "rapid21" if _is_short else "full45"   # label kept for uploads.jsonl continuity
# Anti-stub floor, used by BOTH the expand-retry below AND redditreels' MIN-LENGTH GATE:
# allow genuine short reels (~22s) through but still reject 10-15s stubs.
MIN_WORDS = WORD_BUDGET[0] if _is_short else 70

_SYSTEM_TMPL = """You are a viral short-form scriptwriter. Convert a Reddit post into a __DUR__-second narration that hooks viewers in the first 2 seconds and pays off by the end.

RULES:
- HOOK: First sentence ≤ 12 words, brutal open-loop. Banned openers: "Did you know", "In this video", "Today I", "So I".
- BODY: __WMIN__-__WMAX__ words total (≈ __DUR__ seconds at edge-tts +8% rate). Plain spoken English, present-tense where natural.
- Punctuate for breath. Short sentences. No emojis. No hashtags. No "like and subscribe".
- Preserve the WTF/embarrassing/satisfying core of the original. Strip filler, intros, edits, updates.
- END with a CLIFFHANGER, not a resolution. The last 1-2 sentences must:
   * Tease an unresolved consequence ("She hasn't talked to me since..." / "I'm still waiting to see what happens...")
   * OR explicit follow-bait ("Follow for what happened next" / "Part 2 tomorrow — she found out")
   * NEVER summarize the lesson learned. NEVER close the loop.
   * If the Reddit post has an UPDATE, save it for "part 2" — don't include it here.
- The CLIFFHANGER ending is the #1 lever for follower conversion. The whole script funnels to it.
- BUT only tease consequences that are REAL or genuinely implied by the post — NEVER invent one.

FAITHFULNESS (critical — do NOT fabricate; this is the #1 quality failure):
- Use ONLY events, people, and details that actually appear in the Reddit post. NEVER invent
  characters, actions, letters, phone calls, conversations, timelines, or endings not in the source.
- The TITLE and HOOK must accurately describe what THIS specific post is about — no generic
  clickbait disconnected from the real content.
- A coherent, ACCURATE 50-word script beats a fabricated 120-word one. Accuracy > hitting word count.
- If the post is a pure QUESTION / advice-request with no actual story or events (e.g. "how do I
  make my bf stop doing X"), do NOT manufacture a fake plot. Respond with
  `{"skip": true, "reason": "no narrative — advice/question post"}` so the pipeline fetches a real story.

AD-SAFETY (critical — protects monetization on YouTube/Facebook):
- NEVER use these words (auto-demonetizes): sex, sexy, sexual, orgasm, masturbate, masturbating, porn, smut, smutty, horny, naked, nude, dick, cock, pussy, boobs, tits, fuck, fucking, fucked, shit, shitty, ass, asshole, bitch, damn, hell.
- USE these ad-safe substitutes instead:
    * sex / sexual content → "intimate moment", "spicy moment", "private moment", "hookup", "fooling around"
    * orgasm / climaxing → "the moment", "the peak", "finishing", "the big moment"
    * smut / smutty book → "spicy romance novel", "steamy book", "racy audiobook"
    * masturbating / alone time → "private alone time", "personal moment", "me time"
    * horny → "in the mood", "feeling it"
    * naked / nude → "undressed", "without clothes"
    * fuck / fucked up → "mess", "messed up", "ruined", "blew it", "screwed up"
    * shit → "stuff", "mess", "trash"
    * damn / hell → "wild", "crazy", "insane", "no way"
    * ass / asshole → "jerk", "rude person", "bad person"
- For body parts, use clinical or vague terms: chest, behind, lower body.
- Keep the STORY tension and the WTF moment — just describe it ad-safe.
- If a story is so explicit it can't be made ad-safe (e.g. graphic sex acts as the core hook), respond with the JSON `{"skip": true, "reason": "too explicit for ad-safe rewrite"}` and the pipeline will fetch another.
- ALSO SKIP (same JSON) if the story's CORE is sexual assault, harassment, molestation, groping, stalking, or any non-consensual sexual situation — these are monetization poison and brand-unsafe even when no explicit WORD appears (e.g. "he grabbed me and I felt violated"). When in doubt about a sexual-misconduct theme, skip it.

VIRAL TITLE FORMULA (mandatory — pick ONE pattern):
The title MUST follow one of these patterns. DATA-RANKED by actual cross-platform views (109 videos):
  D) CONFLICT REVEAL — #1 on Facebook (avg 35 views/video vs 0.4 for dead patterns):
     "What happens when [PERSON/EMOTION in ALL-CAPS] [specific conflict]"
     e.g. "What happens when boyfriends LIE #Shorts"
     e.g. "What happens when ENTITLEMENT backfires #Shorts"
     e.g. "What happens when Mom gets ENTITLED #Shorts"
     e.g. "What happens when KAREN loses it #Shorts"
     RULE: the ALL-CAPS word is the emotional hook — pick one that fits: LIE, BETRAY, EXPLODE,
           ENTITLED, EXPOSED, SNAPPED, CAUGHT, REVEALED, BACKFIRE, CHEATS, QUITS, LOSES IT,
           BREAKS DOWN, CROSSES LINE. Keep it punchy and specific to the actual conflict.
  A) NARRATIVE CLIFFHANGER — #2 (avg 17 views, strong on both YT and FB):
     "I [unusual action] — and [unexpected result]"
     e.g. "I left work mid-shift — he's threatening revenge"
     e.g. "I cut off my parents — now they're upset"
  B) CONTRARIAN: "Why [common belief] is actually [opposite]"
     e.g. "Why my 'nice' coworker is the real villain"
  C) NUMBERED REVEAL: "[N] [things/people/moments] that [verb]"
     e.g. "3 words from my boss that ended everything"

BANNED title openers (data-confirmed dead: 0.4 avg views, 0 Facebook reach across 13 videos):
  NEVER start a title with: "You Won't Believe", "You Wont Believe", "Villain?", "VILLAIN?"
  These patterns get zero Facebook distribution and will NOT be used.

Title rules:
  - 35-60 chars (NOT 55 max — 35 is the sweet spot for mobile preview)
  - First 3 words must hook (mobile cuts off ~25 chars)
  - NO clickbait that the script doesn't deliver on
  - End with a noun, action verb, or emotional adjective — NOT a question mark
  - Allowed: em-dash, ellipsis, ALL-CAPS one keyword for emphasis
  - Banned: emojis in title, generic openers ("This is...", "Wow,...", "Story time:")
  - CRITICAL — the title must be a clean, NATURAL English phrase a real person would click:
    * NEVER output literal placeholders or brackets: no "[N]", "[thing]", "[verb]", "[relation]".
      The patterns above are TEMPLATES — fill EVERY slot with real words (use "3", not "[N]").
    * NEVER include a Reddit USERNAME or any token with random digits (e.g. "CartographerKind6228").
    * Must be grammatically correct — no "Why being 'stomping'..." word salad.
    * NO crude/gross words in the title (pissed, fart, etc.) — keep it clean and curiosity-driven.
    * If you can't write a clean compelling title for this story, you may {"skip": true}.

OUTPUT FORMAT — strict JSON:
{
  "hook": "first-sentence hook string",
  "narration": "full __WMIN__-__WMAX__ word script — STRICTLY enforced — hook included as its first sentence",
  "title": "35-60 char title following ONE of patterns A-E above",
  "title_pattern": "A|B|C|D|E (which pattern you used)"
}
"""

# Substitute the duration variant into the template
SYSTEM = (_SYSTEM_TMPL
    .replace("__DUR__", str(TARGET_DURATION_S))
    .replace("__WMIN__", str(WORD_BUDGET[0]))
    .replace("__WMAX__", str(WORD_BUDGET[1])))

# TITLE PATTERN ROTATION — data-driven weights (2026-07-01 audit of 109 runs):
# D ("What happens when") = 35.4 avg views → 4/7 slots
# A (narrative cliffhanger) = 17.0 avg views → 2/7 slots
# B (contrarian) = varied → 1/7 slot for variety
# E (shock statement) REMOVED — generated "You Won't Believe" / "Villain?" patterns
#   that averaged 0.4 views/video and 0 Facebook reach across 13 videos. Eliminated.
# C (numbered reveal) REMOVED from rotation — underperformed vs D/A in practice.
_TITLE_PATTERNS = ["D", "D", "D", "D", "A", "A", "B"]
FORCED_TITLE_PATTERN = _random.choice(_TITLE_PATTERNS)
SYSTEM += (f"\n\nMANDATORY THIS FIRE: use VIRAL TITLE PATTERN {FORCED_TITLE_PATTERN} for the "
           f"title (do NOT default to another pattern) and set \"title_pattern\":\"{FORCED_TITLE_PATTERN}\".")

# SERIES FRAMING (2026-06-25, task C): orient the title/hook around the moral-judgment tension.
if SERIES_ENABLED:
    SYSTEM += ("\n\nSERIES CONTEXT: This is the \"Am I The Villain?\" series — relationship / family / "
               "workplace stories where the narrator might be the bad guy. Frame the TITLE and HOOK "
               "around that moral-judgment tension (who is the villain here?) WITHOUT changing the facts "
               "or inventing anything. Stay fully faithful to the post.")

# Belt-and-suspenders post-filter: even if Claude slips, scrub these words before TTS.
# Maps banned word → ad-safe replacement, case-insensitive whole-word match.
BANNED_REPLACEMENTS = {
    r"\borgasm(s|ed|ing)?\b":     "moment",
    r"\bmasturbat(e|ed|ing|ion)\b":"alone time",
    r"\bsmut(s|ty)?\b":            "spicy",
    r"\bporn(o|ography)?\b":       "spicy content",
    r"\bsexy\b":                   "stunning",
    r"\bsex\b":                    "intimacy",
    r"\bsexual\b":                 "intimate",
    r"\bhorny\b":                  "in the mood",
    r"\bnaked\b":                  "undressed",
    r"\bnude\b":                   "undressed",
    r"\bfuck(ed|ing|er|ers)?\b":   "mess up",
    r"\bshit(s|ty|ting)?\b":       "mess",
    r"\bdamn\b":                   "wild",
    r"\bhell\b":                   "heck",
    r"\bass(hole|holes)?\b":       "jerk",
    r"\bbitch(es|y|ing)?\b":       "rude one",
    r"\bdick(s|head)?\b":          "jerk",
    r"\bcock(s)?\b":               "",
    r"\bpussy\b":                  "",
    r"\bboobs?\b":                 "chest",
    r"\btits?\b":                  "chest",
}


def scrub_text(text: str) -> tuple[str, list]:
    """Replace banned words with ad-safe substitutes. Returns (scrubbed_text, list_of_hits)."""
    import re
    hits = []
    for pat, rep in BANNED_REPLACEMENTS.items():
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        if matches:
            hits.extend(matches if isinstance(matches[0], str) else [m[0] if isinstance(m, tuple) else m for m in matches])
            text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    # Collapse double spaces / clean up empty replacements
    text = re.sub(r"\s+", " ", text).strip()
    return text, hits


def judge_faithful(title: str, narration: str) -> tuple:
    """Semantic title<->story FAITHFULNESS judge (2026-06-25, task B). Returns
    (faithful: bool|None, reason: str). FREE — routes through the llm shim to Groq
    llama-3.3-70b (no paid call). LOG-ONLY for now (observe a week); it NEVER gates or
    re-prompts, and any failure returns (None, ...) so a fire is never broken by it.

    2026-06-30: observation week long past; this 4th Groq call per fire pushes a single
    rewrite over Groq's free per-minute TOKEN budget → 429 under load. Default OFF
    (set RR_JUDGE=1 to re-enable) so every fire is ~25% lighter on Groq."""
    if os.environ.get("RR_JUDGE", "0") != "1":
        return None, "judge disabled (saves Groq quota)"
    try:
        j = client.messages.create(
            model="claude-haiku-4-5", max_tokens=120,
            system=("You are a strict fact-checker for short-video titles. Decide if the TITLE is "
                    "FAITHFUL to the STORY: it must describe events/people that actually appear in the "
                    "story and must not promise a payoff the story never delivers. Paraphrase and "
                    "curiosity are fine; only flag genuine misrepresentation or clickbait the story does "
                    'not support. Respond with STRICT JSON only: {"faithful": true|false, "reason": "<=12 words"}.'),
            messages=[{"role": "user", "content": f"TITLE: {title}\n\nSTORY: {narration}"}],
        )
        t = j.content[0].text.strip()
        if t.startswith("```"):
            t = t.strip("`")
        t = t[t.find("{"):t.rfind("}") + 1]
        v = json.loads(t)
        return bool(v.get("faithful")), str(v.get("reason", ""))[:120]
    except Exception as e:
        return None, f"judge-error: {e}"[:120]


def main():
    s = json.load(open(IN))
    alt_hook_directive = ""
    if os.environ.get("RR_ALT_HOOK"):
        alt_hook_directive = ("\n\nIMPORTANT: A prior version of this story under-performed. "
                              "Write a COMPLETELY DIFFERENT hook + opening angle. Don't repeat the "
                              "previous framing. Try a contrarian or shock-statement opening if the "
                              "first was a narrative cliffhanger, or vice versa.")
    user = f"SUBREDDIT: r/{s['subreddit']}\nPOST TITLE: {s['title']}\nPOST BODY: {s['selftext']}{alt_hook_directive}"
    # Inject weekly_learn insights (closes auto-tuning loop — added 2026-06-03)
    try:
        import sys as _s
        _s.path.insert(0, str(pathlib.Path.home() / "RedditReels/tools"))
        from insights_loader import get_insights_prompt_block
        insights = get_insights_prompt_block()
    except Exception:
        insights = ""
    try:                                       # shared org collective memory
        _s.path.insert(0, str(pathlib.Path.home() / ".project-agents"))
        import collective as _cm
        insights = (insights + "\n\n" + _cm.brief("reels")).strip()
    except Exception:
        pass
    def _gen(extra=""):
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=900,
            system=SYSTEM + insights,
            messages=[{"role": "user", "content": user + extra}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON (tolerate accidental code fences)
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):text.rfind("}")+1]
        elif "{" in text:
            text = text[text.find("{"):text.rfind("}")+1]
        return json.loads(text)

    out = _gen()

    # Claude may decline if story is too explicit to ad-safe-rewrite
    if out.get("skip"):
        reason = out.get("reason", "explicit content")
        print(f"[rewrite_story] SKIP: {reason}")
        # Exit non-zero so the orchestrator can re-fetch
        sys.exit(7)  # 7 = "skip — too explicit"

    # EXPAND-RETRY (2026-06-07): qwen2.5:3b frequently under-writes (<70 words), which
    # used to make the orchestrator REFETCH a new Reddit story — and those repeated
    # ~14-sub RSS sweeps rate-limited Reddit (HTTP 429 → dead fires). Instead, re-prompt
    # the model to EXPAND the SAME story (free, no Reddit hit). Keep the longest draft.
    for _xt in range(3):   # 2026-06-30: 2→3 retries — the model under-writes vs the raised floor
        _wc = len((out.get("narration") or "").split())
        if _wc >= MIN_WORDS:
            break
        print(f"[rewrite_story] only {_wc} words (<{MIN_WORDS}) — asking model to expand (retry {_xt+1})")
        try:
            cand = _gen(f"\n\nYOUR PREVIOUS DRAFT WAS ONLY {_wc} WORDS — a little short. Expand it using "
                        f"ONLY details that are actually in the post — do NOT invent events, people, "
                        f"letters, or endings. Aim for {WORD_BUDGET[0]}-{WORD_BUDGET[1]} words, but ACCURACY "
                        f"beats length: if the post lacks material, keep it shorter rather than fabricate. "
                        f"Stay ad-safe. Return the JSON only.")
            if not cand.get("skip") and len((cand.get("narration") or "").split()) > _wc:
                out = cand
        except Exception as _e:
            print(f"  expand retry failed: {_e}"); break

    # Belt-and-suspenders scrub: catch anything Claude let through
    scrubbed_narration, narration_hits = scrub_text(out["narration"])
    scrubbed_hook,      hook_hits      = scrub_text(out["hook"])
    scrubbed_title,     title_hits     = scrub_text(out["title"])
    all_hits = narration_hits + hook_hits + title_hits

    # TITLE CLEANUP (2026-06-13): the 3B model leaks placeholders / Reddit usernames /
    # crude words into titles ("[N]...", "...CartographerKind6228...", "pissed in a sink").
    # These kill click-through. Strip them; if the title ends up broken, rebuild from the hook.
    import re as _re
    def _clean_title(t, hook):
        t = (t or "").strip()
        # detect junk BEFORE stripping — if present, we rebuild from hook (patching leaves gaps)
        had_junk = bool(_re.search(r"\[[^\]]*\]|\bu/[A-Za-z0-9_-]+\b|[A-Za-z][A-Za-z]+[_-]?\d{2,}", t))
        t = _re.sub(r"\[[^\]]*\]", "", t)                          # [N] / [thing] placeholders
        t = _re.sub(r"\bu/[A-Za-z0-9_-]+\b", "", t)                # u/usernames
        t = _re.sub(r"\b[A-Za-z][A-Za-z]+[_-]?\d{2,}\b", "", t)    # username-like Word6228 tokens
        t = _re.sub(r"\s+'s\b", "", t)                             # orphaned possessive after removal
        t = _re.sub(r"\s{2,}", " ", t).strip(" -—–:|").strip()
        crude = {"pissed","piss","fart","farted","poop","pooped","crap","puke"}
        words = set(_re.findall(r"[a-z]+", t.lower()))
        broken = had_junk or len(t) < 14 or bool(words & crude) or "[" in t or bool(_re.search(r"\d{3,}", t))
        if broken:
            h = _re.sub(r"\s+", " ", (hook or "").strip()).rstrip(".!?")
            # also strip junk from the hook fallback so it's clean
            h = _re.sub(r"\bu/[A-Za-z0-9_-]+\b|[A-Za-z][A-Za-z]+[_-]?\d{2,}|\[[^\]]*\]", "", h)
            h = _re.sub(r"\s+'s\b", "", h); h = _re.sub(r"\s{2,}", " ", h).strip()
            t = (h[:60] if len(h) >= 14 else "A Reddit story that took a wild turn")
        return t[:80]
    scrubbed_title = _clean_title(scrubbed_title, scrubbed_hook)

    # (B) FAITHFULNESS judge — run on the MODEL's real title/story BEFORE any cosmetic series
    # branding, so the verdict reflects the actual generation. Log-only; never gates.
    out["title_faithful"], out["title_faithful_reason"] = judge_faithful(scrubbed_title, scrubbed_narration)
    print(f"[rewrite_story] faithfulness judge: faithful={out['title_faithful']} — {out['title_faithful_reason']}")

    # (C) Apply "Am I The Villain?" series branding.
    # 2026-07-01 DATA UPDATE: "Villain?" TITLE prefix removed from rotation.
    # Cross-platform data (109 runs): "Villain?" titles averaged 1.7 views/video with 0 FB reach.
    # "What happens when" titles averaged 35.4 views/video (32.9 from Facebook).
    # The SPOKEN HOOK stays — it drives engagement comments and series identity in audio.
    # Only the title prefix is removed; the narration still opens with the verbal hook.
    if SERIES_ENABLED:
        if SERIES_SPOKEN_HOOK and not scrubbed_narration.lower().startswith(SERIES_SPOKEN_HOOK.lower()[:14]):
            scrubbed_hook      = f"{SERIES_SPOKEN_HOOK} {scrubbed_hook}".strip()
            scrubbed_narration = f"{SERIES_SPOKEN_HOOK} {scrubbed_narration}".strip()
        # Title prefix intentionally NOT applied — data shows it kills Facebook distribution.

    out["narration"] = scrubbed_narration
    out["hook"]      = scrubbed_hook
    out["title"]     = scrubbed_title
    out["ad_safe_scrubbed"] = sorted(set(all_hits)) if all_hits else []

    word_count = len(out["narration"].split())
    out["word_count"] = word_count
    out["duration_variant"] = DURATION_VARIANT  # logged into uploads.jsonl by orchestrator
    out["target_duration_s"] = TARGET_DURATION_S
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"[rewrite_story] hook: {out['hook']}")
    print(f"  title: {out['title']}")
    print(f"  narration: {word_count} words")
    if all_hits:
        print(f"  ad-safe scrubbed: {out['ad_safe_scrubbed']}")
    print(f"  --- script ---\n{out['narration']}\n---")

if __name__ == "__main__":
    main()
