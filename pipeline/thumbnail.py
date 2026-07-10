#!/usr/bin/env python3
"""
thumbnail.py — generate a high-CTR custom thumbnail for a RedditReels reel.

Why: YT auto-thumbnails pick a random gameplay frame with no context. CTR is the
#1 algorithm signal. A custom thumbnail with the hook text overlaid drives
3-10× higher CTR vs auto.

Layout (1280x720 — YT Shorts thumbnail spec):
  - Background: a striking frame from the rendered reel
  - Heavy darken pass (so text reads)
  - LARGE 2-3 word hook fragment, ALL CAPS, white with thick black stroke
  - Bright yellow accent on 1 key word (matches the in-video caption style)
  - Subtle bottom-left "REDDIT" pill in orange (brand signal)

Inputs:
  reel_path     — final rendered .mp4
  hook_text     — the script's hook line (first sentence ≤12 words)
Output:
  PNG at ~/RedditReels/processing/{ts}/thumb.png
"""
from __future__ import annotations
import os, pathlib, subprocess, tempfile, random, re
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

THUMB_W, THUMB_H = 1280, 720

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
PILL_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _load_font(paths, size):
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except Exception: continue
    return ImageFont.load_default()


def _extract_striking_frame(reel: pathlib.Path, out_jpg: pathlib.Path):
    """Sample several frames from the reel, pick the one with the highest visual
    energy (color variance — proxy for 'interesting moment')."""
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="thumb_"))
    # Sample 8 evenly-spaced frames
    subprocess.check_call([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(reel),
        "-vf", "fps=1/4,scale=640:-1",  # one frame every 4 sec at 640w
        str(tmpdir / "f%03d.jpg")
    ])
    frames = sorted(tmpdir.glob("f*.jpg"))
    if not frames:
        raise RuntimeError("no frames extracted from reel")

    # Try OpenCV face detection — frames with faces get 38% higher CTR (industry data)
    cv2 = None
    try:
        import cv2 as _cv2
        cv2 = _cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
    except Exception:
        face_cascade = None

    best_score = -1
    best = frames[0]
    for f in frames:
        img = Image.open(f).convert("RGB")
        from PIL import ImageStat
        st = ImageStat.Stat(img)
        score = sum(st.stddev)  # visual-energy baseline
        # Face bonus: each detected face adds massive score boost
        if face_cascade is not None:
            try:
                import numpy as _np
                arr = _np.array(img.convert("L"))  # grayscale for face detection
                faces = face_cascade.detectMultiScale(arr, scaleFactor=1.2, minNeighbors=4,
                                                       minSize=(30, 30))
                if len(faces) > 0:
                    score += len(faces) * 500  # strong preference for faces
            except Exception: pass
        if score > best_score:
            best_score = score
            best = f
    import shutil as _sh
    _sh.copy2(best, out_jpg)
    _sh.rmtree(tmpdir, ignore_errors=True)
    return out_jpg


def _extract_thumb_hook(hook_text: str) -> tuple[str, str]:
    """Reduce hook to 2-4 punchy words. Returns (line1, accent_word).

    accent_word = the SHOCK word that gets yellow highlight."""
    words = re.sub(r"[^\w\s]", "", hook_text).split()
    # Common stop words to skip when picking accent
    stops = {"the","a","an","my","your","his","her","their","i","me","we","you",
             "is","was","are","were","be","been","being","have","has","had","do",
             "does","did","of","in","on","at","to","for","with","by","from","up",
             "as","or","and","but","if","then","so","that","this","these","those",
             "just","not","no","yes","also","very","so","into","onto","upon"}
    candidates = [w for w in words if w.lower() not in stops and len(w) >= 4]
    accent = max(candidates, key=len) if candidates else (words[-1] if words else "WAIT")
    # Pick 2-4 words that include the accent, preferring nouns/verbs near the punch
    if accent in words:
        idx = words.index(accent)
        start = max(0, idx - 1)
        end = min(len(words), idx + 2)
        slice_ = words[start:end]
    else:
        slice_ = words[:3]
    line1 = " ".join(slice_).upper()
    return line1, accent.upper()


def build_thumbnail(reel: pathlib.Path, hook_text: str, out_png: pathlib.Path):
    """Render a 1280x720 thumbnail and save as PNG."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_png.parent / "_thumb_frame.jpg"
    _extract_striking_frame(reel, tmp)

    # Background: crop to 16:9 from the reel's 9:16 frame (use center band)
    bg = Image.open(tmp).convert("RGB")
    # Reel is 9:16 → for 16:9 thumb, take the center crop and scale up
    src_w, src_h = bg.size
    # Take a 16:9 slice centered vertically — usually the gameplay action area
    crop_h = src_h // 2
    crop_top = (src_h - crop_h) // 2
    bg = bg.crop((0, crop_top, src_w, crop_top + crop_h))
    bg = bg.resize((THUMB_W, THUMB_H), Image.LANCZOS)

    # AGGRESSIVE color grade for clickbait pop: saturation +, contrast +, vignette darken
    bg = ImageEnhance.Color(bg).enhance(1.45)        # punch saturation
    bg = ImageEnhance.Contrast(bg).enhance(1.30)     # strong contrast
    # Asymmetric darken: bottom 40% darker (where caption + pill sit), top stays bright
    vignette = Image.new("L", bg.size, 255)
    vd = ImageDraw.Draw(vignette)
    for y_v in range(THUMB_H):
        # 100% bright at top, dim toward bottom (creates depth)
        alpha = int(255 * (1.0 - 0.45 * (y_v / THUMB_H) ** 1.8))
        vd.line([(0, y_v), (THUMB_W, y_v)], fill=alpha)
    dark_bg = Image.new("RGB", bg.size, (15, 15, 25))
    bg = Image.composite(bg, dark_bg, vignette)

    canvas = bg.convert("RGBA")
    d = ImageDraw.Draw(canvas)

    # Red SHOCK ARROW pointing from upper-right toward center action area
    # (drives eye to the action zone — classic MrBeast-style technique)
    try:
        arrow_pts = [
            (THUMB_W - 250, 90),   # tail
            (THUMB_W - 180, 90),
            (THUMB_W - 180, 60),
            (THUMB_W - 90, 130),   # arrowhead
            (THUMB_W - 180, 200),
            (THUMB_W - 180, 170),
            (THUMB_W - 250, 170),
        ]
        d.polygon(arrow_pts, fill=(220, 35, 30, 240), outline=(255,255,255,255))
    except Exception:
        pass

    # Reduce hook to a punchy 2-4 word thumbnail phrase
    line1, accent_word = _extract_thumb_hook(hook_text)
    words = line1.split()

    # Layout: large centered text, accent word in yellow
    # Pick a font size that fits within 90% of width
    target_w = int(THUMB_W * 0.92)
    size = 160
    while size > 60:
        f = _load_font(FONT_PATHS, size)
        # Measure with stroke for accurate width
        widths = []
        total = 0
        for w in words:
            bb = d.textbbox((0,0), w+" ", font=f, stroke_width=10)
            widths.append(bb[2]-bb[0])
            total += widths[-1]
        if total <= target_w:
            break
        size -= 8
    f = _load_font(FONT_PATHS, size)
    accent_f = _load_font(FONT_PATHS, size + 10)
    # Recompute widths since accent uses bigger font
    widths = []
    total = 0
    for w in words:
        use_f = accent_f if w == accent_word else f
        bb = d.textbbox((0,0), w+" ", font=use_f, stroke_width=10)
        widths.append(bb[2]-bb[0])
        total += widths[-1]

    x = (THUMB_W - total) // 2
    y = (THUMB_H - size) // 2 - 20

    for w_i, w in enumerate(words):
        use_f = accent_f if w == accent_word else f
        if w == accent_word:
            # Yellow rounded background bar behind the accent word
            bb = d.textbbox((x, y), w, font=accent_f, stroke_width=10)
            d.rounded_rectangle(
                [bb[0]-20, bb[1]-12, bb[2]+20, bb[3]+12],
                radius=20, fill=(255,235,59,255)
            )
            d.text((x, y), w, font=accent_f, fill=(0,0,0,255))
        else:
            d.text((x, y), w, font=f,
                   fill=(255,255,255,255),
                   stroke_width=10, stroke_fill=(0,0,0,255))
        x += widths[w_i]

    # Bottom-left REDDIT pill (orange — Reddit brand color)
    pill_font = _load_font(PILL_FONT_PATHS, 38)
    pill_text = "r/REDDIT STORY"
    pb = d.textbbox((0,0), pill_text, font=pill_font)
    pw, ph = pb[2]-pb[0], pb[3]-pb[1]
    pad = 18
    px0, py0 = 40, THUMB_H - 40 - (ph + pad*2)
    d.rounded_rectangle([px0, py0, px0+pw+pad*2, py0+ph+pad*2], radius=14, fill=(255,69,0,255))
    d.text((px0+pad, py0+pad-8), pill_text, font=pill_font, fill=(255,255,255,255))

    # Top-right "PART 1" tag — implies more content coming (drives FOLLOW)
    part_font = _load_font(PILL_FONT_PATHS, 34)
    part_text = "PART 1"
    pb = d.textbbox((0,0), part_text, font=part_font)
    pw, ph = pb[2]-pb[0], pb[3]-pb[1]
    pad = 14
    px0 = THUMB_W - 40 - (pw + pad*2)
    py0 = 30
    d.rounded_rectangle([px0, py0, px0+pw+pad*2, py0+ph+pad*2], radius=10, fill=(0,0,0,220))
    d.text((px0+pad, py0+pad-6), part_text, font=part_font, fill=(255,235,59,255))

    canvas.convert("RGB").save(out_png, "PNG")
    try: tmp.unlink()
    except Exception: pass
    return out_png


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("usage: thumbnail.py REEL_MP4 HOOK_TEXT OUT_PNG"); sys.exit(1)
    out = build_thumbnail(pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3]))
    print(f"wrote {out}")
