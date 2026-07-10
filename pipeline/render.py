#!/usr/bin/env python3
"""Compose RedditReels demo: full-screen gameplay loop + word-synced karaoke captions + voice + subreddit header."""
import json, math, os, pathlib, random, shutil, subprocess, tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(os.environ.get("RR_ROOT", os.path.expanduser("~/RedditReels")))
WORK = pathlib.Path(os.environ.get("RR_WORK", str(ROOT / "output")))
WORK.mkdir(parents=True, exist_ok=True)

# --- Background pool — random.choice per render so reels don't all look identical ---
# Manifest: filename → human-readable credit (appears in YT description)
BG_MANIFEST = {
    "gameplay_source.mp4": "Background: Minecraft Parkour Gameplay by Orbital - No Copyright Gameplay (CC-BY) — https://youtube.com/watch?v=s600FYgI5-s",
    "bg_gta_74voi0vlxHE.mp4": "Background: GTA 5 Mega Ramp Gameplay by OrbitalNCG (CC-BY) — https://youtube.com/watch?v=74voi0vlxHE",
    "bg_subway_hJcv2nZ8x84.mp4": "Background: Subway Surfers Gameplay by OrbitalNCG (CC-BY) — https://youtube.com/watch?v=hJcv2nZ8x84",
    # legacy fallback
    "gameplay.mp4": "Background: stock gameplay loop",
}

def _pick_bg(rng=None):
    """Return (path, credit_line). Picks at random across whatever bg files exist in clips/."""
    candidates = []
    for name, credit in BG_MANIFEST.items():
        p = ROOT / "clips" / name
        if p.exists() and p.stat().st_size > 1_000_000:
            candidates.append((p, credit))
    if not candidates:
        raise FileNotFoundError(f"no usable bg files in {ROOT/'clips'}")
    return (rng or random).choice(candidates)

# Picked at module load; orchestrator can override via env if it wants determinism
_BG_PATH, _BG_CREDIT = _pick_bg()
GAMEPLAY = _BG_PATH
GAMEPLAY_CREDIT = _BG_CREDIT

NARR_MP3 = WORK/"narration.mp3"
TIMINGS  = WORK/"timings.json"
SCRIPT   = WORK/"script.json"
STORY    = WORK/"story.json"
OUT      = WORK/"reel.mp4"

W, H = 1080, 1920
FPS  = 30

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
HEADER_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
def load_font(paths, size):
    _ov = __import__("os").environ.get("RR_CAPTION_FONT")
    if _ov:
        try: return ImageFont.truetype(_ov, size)
        except Exception: pass
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except Exception: continue
    return ImageFont.load_default()

def run(cmd):
    print("$", " ".join(str(c) for c in cmd[:8]) + (" …" if len(cmd) > 8 else ""))
    subprocess.check_call(cmd)

def ffprobe_dur(p):
    out = subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0", str(p)])
    return float(out.strip())

# ---------- captions: chunk words into 1-3 word phrases ----------

def chunk_timings(timings, max_words=3):
    """Group word timings into short chunks. Break on punctuation or pause > 0.35s."""
    chunks = []
    cur = []
    for i, w in enumerate(timings):
        cur.append(w)
        word = w["word"]
        ends_punct = bool(word) and word[-1] in ",.!?;:"
        next_gap = (timings[i+1]["start"] - w["end"]) if i+1 < len(timings) else 999
        if len(cur) >= max_words or ends_punct or next_gap > 0.35:
            chunks.append({
                "words": cur,
                "text": " ".join(x["word"] for x in cur),
                "start": cur[0]["start"],
                "end":   cur[-1]["end"],
            })
            cur = []
    if cur:
        chunks.append({
            "words": cur,
            "text": " ".join(x["word"] for x in cur),
            "start": cur[0]["start"],
            "end":   cur[-1]["end"],
        })
    return chunks

# ---------- per-chunk PNG (centered, drop-shadow, yellow highlight on active word) ----------

def render_chunk_pngs(chunks, out_dir, subreddit=None):
    """TikTok-style captions: big bold font, thick black stroke, yellow highlight on active word.
    No subreddit pill — pure text drives attention."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base       = load_font(FONT_PATHS, 124)
    accent     = load_font(FONT_PATHS, 138)   # active word slightly larger = pulse
    STROKE     = 11
    LINE_H     = 150
    YELLOW     = (255, 235, 59, 245)
    pngs = []
    for ci, c in enumerate(chunks):
        for wi, w in enumerate(c["words"]):
            img = Image.new("RGBA",(W,H),(0,0,0,0))
            d   = ImageDraw.Draw(img)

            text = c["text"].upper()  # ALL-CAPS for impact
            words = text.split()
            # Wrap if measured wider than 88% of frame
            line1, line2 = text, ""
            full_bbox = d.textbbox((0,0), text, font=base, stroke_width=STROKE)
            if (full_bbox[2]-full_bbox[0]) > int(W*0.88) and len(words) > 1:
                mid = len(words)//2
                line1, line2 = " ".join(words[:mid]), " ".join(words[mid:])
            lines = [line1] + ([line2] if line2 else [])

            # Vertical center for the block
            block_h = LINE_H * len(lines)
            line_y  = (H - block_h)//2 + 40   # slight south-of-center bias

            for li, line in enumerate(lines):
                line_words = line.split()
                # measure all words on this line using ACTIVE font for the active one so layout doesn't shift jarringly
                widths = []
                total = 0
                for lw_i, lw in enumerate(line_words):
                    global_idx = sum(len(l.split()) for l in lines[:li]) + lw_i
                    is_active = (global_idx == wi)
                    f = accent if is_active else base
                    bb = d.textbbox((0,0), lw+" ", font=f, stroke_width=STROKE)
                    ww = bb[2]-bb[0]
                    widths.append(ww)
                    total += ww
                x = (W - total)//2
                for lw_i, lw in enumerate(line_words):
                    global_idx = sum(len(l.split()) for l in lines[:li]) + lw_i
                    is_active = (global_idx == wi)
                    if is_active:
                        # Yellow rounded background bar behind active word
                        bb = d.textbbox((x,line_y), lw, font=accent, stroke_width=STROKE)
                        d.rounded_rectangle(
                            [bb[0]-18, bb[1]-12, bb[2]+18, bb[3]+12],
                            radius=18, fill=YELLOW
                        )
                        # Black text on yellow (no stroke — clean)
                        d.text((x,line_y), lw, font=accent, fill=(0,0,0,255))
                    else:
                        # White text with thick black stroke = legible on ANY background
                        d.text(
                            (x,line_y), lw, font=base,
                            fill=(255,255,255,255),
                            stroke_width=STROKE, stroke_fill=(0,0,0,255),
                        )
                    x += widths[lw_i]
                line_y += LINE_H

            p = out_dir/f"cap_{ci:03d}_{wi:02d}.png"
            img.save(p)
            pngs.append({"path": str(p), "start": w["start"], "end": w["end"]})
    return pngs

# ---------- ffmpeg compose ----------

def build_loop_bg(narration_dur, out_path):
    """Pick a random window from gameplay source to cover narration_dur, scale-crop to 1080x1920, mute.

    Adds PATTERN INTERRUPTS at 3s/8s/15s — the documented retention drop-off cliffs on
    Shorts. A 0.15s zoom-punch (scale to 1.08x, hold briefly) jolts viewer attention back
    and pulls them past the cliff. Boosts retention 5-15% per industry data."""
    src_dur = ffprobe_dur(GAMEPLAY)
    need = narration_dur + 1.0
    if src_dur >= need + 5:
        start = random.uniform(2.0, src_dur - need - 2.0)
        loops = 0
        print(f"  source {src_dur:.1f}s >= need {need:.1f}s → random window from {start:.1f}s")
    else:
        start = 0.0
        loops = max(0, math.ceil(need / src_dur))
        print(f"  source {src_dur:.1f}s < need {need:.1f}s → looping {loops}x")

    # Pattern-interrupt zoom-punches at 3s, 8s, 15s (only if narration covers each)
    # Use ffmpeg's `zoompan` is complex; simpler = a chain of `scale` + crop with time-keyed
    # enable conditions, using the `lutyuv` or `scale` with time-varying parameters.
    # Simplest reliable approach: apply a brief eq+saturate punch at each cliff.
    interrupt_pulses = []
    for t in (3.0, 8.0, 15.0):
        if t + 0.3 < narration_dur:
            # 0.15s pulse: brighter + slight saturation bump
            interrupt_pulses.append(
                f"eq=brightness=0.04:saturation=1.35:enable='between(t,{t},{t+0.15})'"
            )
    pulse_chain = "," + ",".join(interrupt_pulses) if interrupt_pulses else ""

    fc = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"eq=saturation=1.10:contrast=1.05"
        f"{pulse_chain}"
        f"[v]"
    )
    cmd = [
        "ffmpeg","-y","-loglevel","error",
        "-ss", f"{start:.3f}",
    ]
    if loops:
        cmd += ["-stream_loop", str(loops)]
    cmd += [
        "-i", str(GAMEPLAY),
        "-t", f"{narration_dur:.3f}",
        "-filter_complex", fc, "-map","[v]",
        "-r", str(FPS), "-an",
        "-c:v","libx264","-preset","veryfast","-crf","20", str(out_path)
    ]
    if interrupt_pulses:
        print(f"  pattern-interrupts: {len(interrupt_pulses)} pulses at retention cliffs")
    run(cmd)

def overlay_captions(video_in, pngs, out_path):
    """Overlay each caption PNG only during its word's time range. Chained overlays."""
    inputs = ["-i", str(video_in)]
    for p in pngs:
        inputs += ["-i", p["path"]]
    filters = []
    last = "0:v"
    for i, p in enumerate(pngs):
        lbl = f"v{i}"
        filters.append(
            f"[{last}][{i+1}:v]overlay=0:0:enable='between(t,{p['start']:.3f},{p['end']:.3f})'[{lbl}]"
        )
        last = lbl
    fc = ";".join(filters)
    run([
        "ffmpeg","-y","-loglevel","error", *inputs,
        "-filter_complex", fc, "-map", f"[{last}]",
        "-c:v","libx264","-preset","veryfast","-crf","20","-r",str(FPS), str(out_path)
    ])

def mux_audio(video_in, audio_in, out_path):
    run([
        "ffmpeg","-y","-loglevel","error",
        "-i", str(video_in), "-i", str(audio_in),
        "-c:v","copy", "-c:a","aac","-b:a","160k","-shortest", str(out_path)
    ])


def make_loopable(video_in, out_path, freeze_secs: float = 0.4):
    """Append a brief frame-0 freeze at the end so the loop point feels intentional.
    YT's algo rewards 'rewatches' — making the end visually echo the start tricks
    the algo into reading auto-restart as engagement vs disengagement.

    BUGFIX 2026-05-30: previous version used afade=t=out:st=freeze_secs/2:d=freeze_secs/2
    which interpreted 'st' as offset from START of audio, faded voice to silence after
    just 0.4s. Removed afade entirely — apad adds silence after voice ends, which is
    fine because the freeze frame itself is silent."""
    fc = (
        f"[0:v]split=2[main][seed];"
        f"[seed]trim=0:0.05,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={freeze_secs}[freeze];"
        f"[main][freeze]concat=n=2:v=1:a=0[v];"
        f"[0:a]apad=pad_dur={freeze_secs}[a]"
    )
    run([
        "ffmpeg","-y","-loglevel","error",
        "-i", str(video_in),
        "-filter_complex", fc, "-map","[v]", "-map","[a]",
        "-c:v","libx264","-preset","veryfast","-crf","20",
        "-c:a","aac","-b:a","160k", str(out_path)
    ])

def build_engagement_overlays(out_dir, narration_dur):
    """Build engagement + attention PNG overlays:
      - "WAIT FOR IT..." at the START (0.0-0.5s) — scroll-stopping attention grabber
      - "COMMENT ↓" at 60% of narration (peak attention drop-off point)
      - "FOLLOW ↑" at 85% of narration (right before cliffhanger payoff)
      - "FOLLOW FOR PART 2 →" big end-card overlay (last 1.8s)
    Returns [{path, start, end}] for the overlay chain."""
    out_dir.mkdir(exist_ok=True, parents=True)
    pngs = []
    # 2026-06-03 overnight: rotate through 7 first-frame hook templates so the
    # same overlay doesn't burn out from algorithm exposure (FB+YT both detect
    # repeated frame patterns and may de-prioritize).
    import random as _r
    FIRST_FRAME_TEMPLATES = [
        "⚠️ WAIT FOR IT...",
        "🔴 DO NOT scroll",
        "👀 You won't believe...",
        "❗ Stay till the END",
        "🚫 Don't skip this",
        "🤯 This actually happened",
        "🛑 Pause and read",
    ]
    first_frame = _r.choice(FIRST_FRAME_TEMPLATES)
    spec = [
        # Scroll-stopping first-frame grab (BEFORE voice starts)
        (first_frame, "center_top",       0.0, 0.6),
        ("COMMENT ↓ which side were you on?", "bottom",
         narration_dur * 0.60, narration_dur * 0.60 + 3.5),
        ("↑ FOLLOW for part 2", "top",
         narration_dur * 0.85, narration_dur * 0.85 + 3.0),
        # Massive end-card follow CTA (last 1.8s)
        ("👇 FOLLOW for PART 2", "center",
         max(0.0, narration_dur - 1.8), narration_dur + 0.3),
    ]
    base_font = load_font(FONT_PATHS, 72)
    big_font  = load_font(FONT_PATHS, 110)  # for end-card FOLLOW CTA
    for i, (text, pos, start, end) in enumerate(spec):
        img = Image.new("RGBA",(W,H),(0,0,0,0))
        d   = ImageDraw.Draw(img)
        # Big font for "FOLLOW for PART 2" end-card
        is_endcard = pos == "center"
        is_first_grab = pos == "center_top"
        font = big_font if is_endcard else base_font
        STROKE = 12 if is_endcard else 8
        bbox = d.textbbox((0,0), text, font=font, stroke_width=STROKE)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pad = 28
        box_w, box_h = tw + pad*2, th + pad*2
        x0 = (W - box_w) // 2
        if pos == "top":
            y0 = 220
        elif pos == "bottom":
            y0 = H - 380 - box_h
        elif pos == "center":
            y0 = (H - box_h) // 2
        elif pos == "center_top":
            y0 = int(H * 0.30)  # upper third — visible above gameplay
        else:
            y0 = (H - box_h) // 2
        # Color scheme: endcard = bright red (urgent), first_grab = bright orange (attention)
        if is_endcard:
            fill = (220, 30, 30, 250)
        elif is_first_grab:
            fill = (255, 100, 0, 245)
        else:
            fill = (255, 213, 0, 245)
        d.rounded_rectangle([x0, y0, x0+box_w, y0+box_h], radius=22, fill=fill)
        text_color = (255,255,255,255) if (is_endcard or is_first_grab) else (0,0,0,255)
        d.text((x0+pad, y0+pad), text, font=font, fill=text_color)
        p = out_dir / f"engage_{i}_{pos}.png"
        img.save(p)
        pngs.append({"path": str(p), "start": start, "end": end})
    return pngs


def main():
    timings = json.load(open(TIMINGS))
    story   = json.load(open(STORY))
    narration_dur = timings[-1]["end"] + 0.3
    print(f"[render] narration {narration_dur:.2f}s, {len(timings)} words, r/{story['subreddit']}")
    print(f"  bg pick: {GAMEPLAY.name}")

    # Persist bg credit so the orchestrator can include it in YT description
    (WORK / "bg_credit.txt").write_text(GAMEPLAY_CREDIT)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="redditreels_"))
    print(f"  tmp = {tmp}")

    print("[render] 1/4 gameplay loop → 1080x1920")
    bg = tmp/"bg.mp4"
    build_loop_bg(narration_dur, bg)

    print("[render] 2/4 build chunk + word PNGs + engagement overlays")
    chunks = chunk_timings(timings, max_words=2)
    print(f"  → {len(chunks)} chunks across {len(timings)} words")
    pngs = render_chunk_pngs(chunks, tmp/"caps")
    # Engagement overlays at 60% (Comment) + 85% (Follow) of narration
    engage_pngs = build_engagement_overlays(tmp/"engage", narration_dur)
    print(f"  → {len(pngs)} caption frames + {len(engage_pngs)} engagement overlays")
    # Merge — engagement appears AS overlays alongside captions
    all_pngs = pngs + engage_pngs

    print("[render] 3/4 overlay captions + engagement")
    capped = tmp/"capped.mp4"
    overlay_captions(bg, all_pngs, capped)

    print("[render] 4/5 mux narration audio")
    muxed = tmp/"muxed.mp4"
    mux_audio(capped, NARR_MP3, muxed)

    print("[render] 5/5 add loopable end-freeze for YT 'rewatch' algo signal")
    try:
        make_loopable(muxed, OUT, freeze_secs=0.4)
    except Exception as e:
        print(f"  loopable post-process failed ({e}); falling back to muxed")
        shutil.copy2(muxed, OUT)

    print(f"[render] DONE → {OUT}  ({OUT.stat().st_size/1024/1024:.1f} MB, {narration_dur:.1f}s)")
    shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
