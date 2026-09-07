#!/usr/bin/env python3
"""
Vibte TikTok generator — matches the "Accept vs Except" HTML slide
reference exactly: same 6 slide types, same design tokens, same layout
rules, same icon language. To make a new episode, edit EPISODE below —
everything else (rendering, TTS, ffmpeg assembly) is reusable as-is.
1080x1920, 30fps, GuyNeural 1.0x.
"""
import json, os, math, glob, subprocess, urllib.request
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = "/tmp/vibte_accept_vs_except_pipeline"
OUT_VIDEO = "/tmp/vibte_accept_vs_except.mp4"
FRAMES_DIR = os.path.join(OUT_ROOT, "frames")
AUDIO_DIR = os.path.join(OUT_ROOT, "audio")
REGISTRY = os.environ.get("VIBTE_REGISTRY", os.path.join(SKILL_DIR, "episodes_registry.json"))
NINEROUTER = os.environ.get("NINEROUTER_URL", "http://127.0.0.1:20128/v1")
NINEROUTER_API_KEY = os.environ.get("NINEROUTER_API_KEY", "")
FONT_B = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
FONT_I = "/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf"  # used for example-sentence italics
W, H, FPS = 1080, 1920, 30

# ── Global brand tokens (fixed across every episode) ──
BG = (12, 15, 36)              # --bg-color #0c0f24
WHITE = (255, 255, 255)
GREEN = (60, 208, 112)         # --correct-green
MUTED = (148, 163, 184)        # --text-muted #94a3b8
CARD_BG_ALPHA = 8              # rgba(255,255,255,0.03) ~ alpha 8/255

LOGO_URL = "https://res.cloudinary.com/dghs3hg9h/image/upload/v1771159380/Vibte_LOGO_x1hogx.png"
LOGO_CACHE = os.path.join(OUT_ROOT, "vibte_logo.png")
LOGO_HEIGHT = 96                # "2x" logo per reference: max-height 96px
LOGO_MARGIN_X, LOGO_MARGIN_Y = 40, 36

# ── Episode-specific config — this is the only block to change per video ──
# CANONICAL TEMPLATE EPISODE: "Already vs All Ready" — user-approved best layout.
# Copy this block, change the content values, and re-run. Rendering code below
# stays identical. Colors are per-word-pair but always use the same two roles:
# word1 = blue-ish accent, word2 = orange-ish accent.
EPISODE = {
    "level": "LEVEL B1",
    "subtitle": "Commonly Confused Words",
    "word1": "ALREADY", "word1_color": (56, 189, 248),      # --blue #38bdf8
    "word2": "ALL READY", "word2_color": (255, 130, 58),    # --orange #ff823a
    "word1_pos": "Adverb",
    "word1_def": "Before now or earlier",
    "word1_example": '"She already left for work."',
    "word1_icon": "checkmark",
    "word2_pos": "Phrase",
    "word2_def": "Everyone is prepared",
    "word2_example": '"We are all ready to go."',
    "word2_icon": "group",
    "cards_header": "ALREADY vs ALL READY",
    "word1_card_sub": "Before that time",
    "word1_card_lines": ["Already done", "Earlier than expected"],
    "word2_card_sub": "Everyone prepared",
    "word2_card_lines": ["Fully ready", "All of us are set"],
    "quiz_title": "Quick Quiz",
    "quiz_question": ["Are you ___ to leave, or do you", "need another minute?"],
    "quiz_prompt": "Which one fits?",
    "result_line1": "ALREADY = before now",
    "result_line2": "ALL READY = everyone prepared",
    "promo_link": "vibte.com",
    "promo_tagline": "Already vs All Ready — Know the difference.",
    "tts": [
        "B1 English: already versus all ready.",
        "Already means before now or earlier. She already left for work.",
        "All ready means everyone or everything is prepared. We are all ready to go.",
        "Already is about time. All ready is about preparation. Know the difference.",
        "Quiz: Are you blank to leave, or do you need another minute?",
        "Answer: all ready. Already means before now, all ready means prepared.",
    ],
    "episode_id_slug": "already-vs-allready",
    "word_pair": ["already", "all ready"],
}

TOTAL_SLIDES = 6

os.makedirs(FRAMES_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ── Step 1: TTS ──
print("=== Step 1: TTS Generation ===", flush=True)
tts_durations, tts_paths = [], []

for i, text in enumerate(EPISODE["tts"]):
    out = os.path.join(AUDIO_DIR, f"scene_{i:03d}.mp3")
    try:
        kw = {}
        if NINEROUTER_API_KEY:
            kw["-H"] = "Authorization: Bearer " + NINEROUTER_API_KEY
        subprocess.run(["curl", "-s", "-X", "POST", f"{NINEROUTER}/audio/speech",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"model": "edge-tts/en-US-GuyNeural", "input": text}),
            "--max-time", "25", "--output", out] + ([
                "-H", "Authorization: Bearer " + NINEROUTER_API_KEY
            ] if NINEROUTER_API_KEY else []), timeout=30, check=True)
        if os.path.getsize(out) < 500:
            raise RuntimeError(f"TTS response too small ({os.path.getsize(out)} bytes) — likely auth error")
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", out], capture_output=True, text=True, timeout=10)
        dur = float(r.stdout.strip() or 2.0)
    except Exception as e:
        print(f"  TTS {i} failed: {e}", flush=True)
        dur = 3.0
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(dur), "-c:a", "libmp3lame", out], capture_output=True, timeout=10)
    tts_durations.append(dur)
    tts_paths.append(out)
    print(f"  Scene {i}: {dur:.2f}s — '{text[:50]}...'", flush=True)

total_tts = sum(tts_durations)
print(f"  TTS total: {total_tts:.2f}s", flush=True)
if total_tts > 32:
    print(f"  \u26a0\ufe0f  TOO LONG at {total_tts:.2f}s (max 32s). Will need cuts!", flush=True)

# ── Step 2: Frame Generation ──
print("=== Step 2: Render Frames ===", flush=True)

from PIL import Image, ImageDraw, ImageFont
import numpy as np

font_cache = {}
def font(size, italic=False):
    key = (size, italic)
    if key not in font_cache:
        path = FONT_I if italic and os.path.exists(FONT_I) else FONT_B
        font_cache[key] = ImageFont.truetype(path, size)
    return font_cache[key]

for old in glob.glob(os.path.join(FRAMES_DIR, "*.ppm")):
    os.remove(old)

def load_logo():
    if not os.path.exists(LOGO_CACHE):
        try:
            print("  Downloading Vibte logo...", flush=True)
            urllib.request.urlretrieve(LOGO_URL, LOGO_CACHE)
        except Exception as e:
            print(f"  Cannot download logo, skipping: {e}", flush=True)
            return None
    try:
        logo = Image.open(LOGO_CACHE).convert("RGBA")
    except Exception as e:
        print(f"  Cannot open logo, skipping: {e}", flush=True)
        return None
    ratio = LOGO_HEIGHT / logo.height
    new_w = max(1, int(logo.width * ratio))
    return logo.resize((new_w, LOGO_HEIGHT), Image.LANCZOS)

LOGO_IMG = load_logo()

def shadow_text(draw, x, y, text, f, fill, shadow_offset=3, shadow_alpha=170):
    if shadow_offset:
        draw.text((x + shadow_offset, y + shadow_offset), text, font=f, fill=(0, 0, 0, shadow_alpha))
    draw.text((x, y), text, font=f, fill=(*fill, 255))

def text_w(f, text):
    bb = f.getbbox(text)
    return bb[2] - bb[0]

def centered_text(draw, y_abs, text, f, color, **kw):
    shadow_text(draw, (W - text_w(f, text)) // 2, y_abs, text, f, color, **kw)

def multiline_centered(draw, y_start, lines, f, color, line_gap=8, **kw):
    y = y_start
    lh = f.getbbox("Ag")[3] + line_gap
    for line in lines:
        centered_text(draw, y, line, f, color, **kw)
        y += lh
    return y

def slide_number(draw, i):
    txt = f"Slide {i+1}"
    f = font(22)
    draw.text((W - 40 - text_w(f, txt), 30), txt, font=f, fill=(255, 255, 255, 64))

def draw_logo(frame):
    if LOGO_IMG is None:
        return
    frame.alpha_composite(LOGO_IMG, (LOGO_MARGIN_X, LOGO_MARGIN_Y))

def draw_icon_box(draw, cx, cy, color, kind, s=96):
    x0, y0 = cx - s // 2, cy - s // 2
    draw.rounded_rectangle([x0, y0, x0 + s, y0 + s], radius=22,
                            outline=(*color, 255), width=3, fill=(*BG, 40))
    if kind == "inbox":
        # downward arrow feeding into an open-top tray, like lucide "inbox"
        draw.line([(cx, cy - 26), (cx, cy + 4)], fill=(*color, 255), width=4)
        draw.line([(cx - 12, cy - 8), (cx, cy + 4), (cx + 12, cy - 8)], fill=(*color, 255), width=4, joint="curve")
        draw.line([(cx - 20, cy + 10), (cx - 20, cy + 24), (cx + 20, cy + 24), (cx + 20, cy + 10)],
                   fill=(*color, 255), width=4, joint="curve")
    else:  # "exclude" — circle with a diagonal slash, like a "not allowed" glyph
        r = 22
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*color, 255), width=4)
        d = r * 0.72
        draw.line([(cx - d, cy + d), (cx + d, cy - d)], fill=(*color, 255), width=4)

def draw_badge_pill(draw, x, y, letter, color):
    f = font(20)
    pad_x, pad_y = 8, 4
    tw = text_w(f, letter)
    draw.rounded_rectangle([x, y, x + tw + pad_x * 2, y + f.getbbox(letter)[3] + pad_y * 2],
                            radius=4, outline=(*color, 255), width=1)
    draw.text((x + pad_x, y + pad_y), letter, font=f, fill=(*color, 255))
    return tw + pad_x * 2

def draw_check_box(draw, x, y, size, color):
    draw.rounded_rectangle([x, y, x + size, y + size], radius=max(4, size // 5), outline=(*color, 255), width=3)
    s = size * 0.26
    cx2, cy2 = x + size / 2, y + size / 2
    draw.line([(cx2 - s, cy2), (cx2 - s * 0.25, cy2 + s * 0.65), (cx2 + s, cy2 - s * 0.65)],
              fill=(*color, 255), width=3, joint="curve")

def draw_card(draw, cx, cy, w, h, color):
    x1, y1, x2, y2 = cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2
    draw.rounded_rectangle([x1, y1, x2, y2], radius=18, fill=(255, 255, 255, CARD_BG_ALPHA),
                            outline=(*color, 255), width=3)

# ── Slide renderers (one function per HTML slide type) ──

def render_intro(draw, ep):
    cy = H // 2
    centered_text(draw, cy - 340, ep["level"], font(100), ep["word1_color"])
    centered_text(draw, cy - 220, ep["subtitle"], font(34), MUTED)
    lx = (W - 240) // 2
    draw.line([(lx, cy - 150), (lx + 240, cy - 150)], fill=(255, 255, 255, 40), width=2)
    centered_text(draw, cy - 60, ep["word1"], font(78), ep["word1_color"])
    centered_text(draw, cy + 40, "OR", font(30), MUTED)
    centered_text(draw, cy + 110, ep["word2"], font(78), ep["word2_color"])

def render_word_detail(draw, word, pos, definition, example, color, icon_kind):
    top = 300
    centered_text(draw, top, word, font(78), color)
    centered_text(draw, top + 96, pos, font(32), MUTED)
    centered_text(draw, top + 230, definition, font(46), WHITE)
    draw_icon_box(draw, W // 2, top + 430, color, icon_kind)
    centered_text(draw, top + 540, example, font(34, italic=True), WHITE)

def render_cards(draw, ep):
    centered_text(draw, 220, ep["cards_header"], font(42), ep["word1_color"])

    card_w, card_h, gap = 460, 560, 24
    cy = H // 2 + 120
    left_cx = W // 2 - card_w // 2 - gap // 2
    right_cx = W // 2 + card_w // 2 + gap // 2

    f_title, f_sub, f_line = font(46), font(30), font(26)

    for cx, color, title, sub, lines in (
        (left_cx, ep["word1_color"], ep["word1"], ep["word1_card_sub"], ep["word1_card_lines"]),
        (right_cx, ep["word2_color"], ep["word2"], ep["word2_card_sub"], ep["word2_card_lines"]),
    ):
        # Card shell: rgba(18,24,48,0.75) fill + colored 2px border, radius 20 — per updated CSS
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2
        draw.rounded_rectangle([x1, y1, x2, y2], radius=20,
                                fill=(18, 24, 48, 191), outline=(*color, 255), width=2)

        # Build the content block height first so it can sit centered inside the card
        th = f_title.getbbox(title)[3]
        sh = f_sub.getbbox(sub)[3]
        lh = f_line.getbbox("Ag")[3]
        gap_title, gap_sub, gap_div = 12, 16, 20
        line_gap = 10
        divider_h = 2
        block_h = th + gap_title + sh + gap_sub + divider_h + gap_div + len(lines) * lh + (len(lines) - 1) * line_gap
        y = cy - block_h // 2

        shadow_text(draw, cx - text_w(f_title, title) // 2, y, title, f_title, color)
        y += th + gap_title
        shadow_text(draw, cx - text_w(f_sub, sub) // 2, y, sub, f_sub, WHITE)
        y += sh + gap_sub

        # card-divider: 48px wide, 2px, faint white line, centered
        draw.line([(cx - 24, y + divider_h // 2), (cx + 24, y + divider_h // 2)], fill=(255, 255, 255, 46), width=2)
        y += divider_h + gap_div

        for line in lines:
            shadow_text(draw, cx - text_w(f_line, line) // 2, y, line, f_line, MUTED)
            y += lh + line_gap

def render_quiz(draw, ep):
    top = 240
    centered_text(draw, top, ep["quiz_title"], font(52), ep["word1_color"])
    y = multiline_centered(draw, top + 140, ep["quiz_question"], font(48), WHITE, line_gap=16)

    btn_w, btn_h, gap = 360, 120, 40
    y_c = y + 80
    for side, label, bx0 in (
        ("A", ep["word1"].lower(), W // 2 - btn_w - gap // 2),
        ("B", ep["word2"].lower(), W // 2 + gap // 2),
    ):
        # Transparent box — white border only, no fill, both same
        draw.rounded_rectangle([bx0, y_c, bx0 + btn_w, y_c + btn_h], radius=16,
                                fill=None, outline=(255, 255, 255, 100), width=2)
        pw = draw_badge_pill(draw, bx0 + 26, y_c + 32, side, WHITE)
        f = font(40)
        shadow_text(draw, bx0 + 26 + pw + 14, y_c + 44, label, f, WHITE)

    centered_text(draw, y_c + btn_h + 40, ep["quiz_prompt"], font(32), MUTED)

def render_result(draw, ep):
    cy = 300
    r = 26
    draw.ellipse([W // 2 - r, cy - r, W // 2 + r, cy + r], fill=(*GREEN, 255))
    centered_text(draw, cy + r + 24, "Correct!", font(44), GREEN)

    box = 40
    for i, (label, color) in enumerate((
        (ep["result_line1"], ep["word1_color"]),
        (ep["result_line2"], ep["word2_color"]),
    )):
        y = 560 + i * 110
        f = font(34)
        tw = text_w(f, label)
        total_w = box + 20 + tw
        x0 = (W - total_w) // 2
        draw_check_box(draw, x0, y, box, color)
        shadow_text(draw, x0 + box + 20, y + (box - (f.getbbox(label)[3])) // 2, label, f, color)

    centered_text(draw, H - 360, ep["promo_link"], font(44), ep["word1_color"])
    centered_text(draw, H - 300, ep["promo_tagline"], font(28), MUTED)

RENDERERS = [
    lambda draw, ep: render_intro(draw, ep),
    lambda draw, ep: render_word_detail(draw, ep["word1"], ep["word1_pos"], ep["word1_def"],
                                         ep["word1_example"], ep["word1_color"], ep["word1_icon"]),
    lambda draw, ep: render_word_detail(draw, ep["word2"], ep["word2_pos"], ep["word2_def"],
                                         ep["word2_example"], ep["word2_color"], ep["word2_icon"]),
    lambda draw, ep: render_cards(draw, ep),
    lambda draw, ep: render_quiz(draw, ep),
    lambda draw, ep: render_result(draw, ep),
]

frame_idx = 0
for scene_i in range(TOTAL_SLIDES):
    dur = tts_durations[scene_i]
    nf = max(2, int(dur * FPS))

    frame = Image.new("RGBA", (W, H), (*BG, 255))
    draw = ImageDraw.Draw(frame, "RGBA")

    RENDERERS[scene_i](draw, EPISODE)
    slide_number(draw, scene_i)
    draw_logo(frame)

    frame_rgb = frame.convert("RGB")
    fp = os.path.join(FRAMES_DIR, f"frame_{frame_idx:06d}.ppm")
    frame_rgb.save(fp)
    if nf > 1:
        for f_i in range(1, nf):
            os.link(fp, os.path.join(FRAMES_DIR, f"frame_{frame_idx + f_i:06d}.ppm"))
    frame_idx += nf
    print(f"  Scene {scene_i}: {nf}f = {dur:.2f}s", flush=True)

total_frames = frame_idx
total_dur_video = total_frames / FPS
print(f"  Total: {total_frames}f = {total_dur_video:.1f}s", flush=True)

# ── Step 3: Audio concat + normalise ──
print("=== Step 3: Audio Assembly ===", flush=True)
concat_list = os.path.join(AUDIO_DIR, "list.txt")
with open(concat_list, "w") as f:
    for p in tts_paths:
        if os.path.getsize(p) > 100:
            f.write(f"file '{os.path.abspath(p)}'\n")

audio_cat = os.path.join(AUDIO_DIR, "concat.mp3")
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
    "-c:a", "libmp3lame", "-q:a", "2", audio_cat], capture_output=True, timeout=60)

audio_final = os.path.join(AUDIO_DIR, "final.mp3")
subprocess.run(["ffmpeg", "-y", "-i", audio_cat,
    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
    "-c:a", "libmp3lame", "-q:a", "2", audio_final], capture_output=True, timeout=60)

r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", audio_final], capture_output=True, text=True, timeout=10)
audio_dur = float(r.stdout.strip() or 0)
print(f"  Audio duration: {audio_dur:.2f}s", flush=True)

# ── Step 4: Assemble Video ──
print("=== Step 4: Video Assembly ===", flush=True)
subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS),
    "-i", os.path.join(FRAMES_DIR, "frame_%06d.ppm"),
    "-i", audio_final,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
    "-crf", "18", "-b:v", "5M", "-maxrate", "8M", "-bufsize", "10M",
    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
    OUT_VIDEO], capture_output=True, timeout=300)

fsize = os.path.getsize(OUT_VIDEO) / (1024 * 1024) if os.path.exists(OUT_VIDEO) else 0
final_dur = min(total_dur_video, audio_dur)

print(f"\n{'='*50}", flush=True)
print(f"  \u2705 {OUT_VIDEO}", flush=True)
print(f"     Duration: {final_dur:.1f}s", flush=True)
print(f"     Size: {fsize:.1f}MB", flush=True)
print(f"     Frames: {total_frames} @ {FPS}fps", flush=True)

if final_dur > 32:
    print(f"  \u26a0\ufe0f  OVER 32s ({final_dur:.1f}s). Need cuts!", flush=True)
elif final_dur > 30:
    print(f"  \u26a0\ufe0f  Over preferred 30s ({final_dur:.1f}s), under 32s cap.", flush=True)
else:
    print("  \u2705 Within 27-30s target.", flush=True)

# ── Step 5: Post-Render Contact Sheet ──
print("\n=== Step 5: Post-Render Frame Inspection ===", flush=True)
contact_dir = os.path.join(OUT_ROOT, "contact_sheet")
os.makedirs(contact_dir, exist_ok=True)

sample_positions = []
idx = 0
for scene_i in range(TOTAL_SLIDES):
    dur = tts_durations[scene_i]
    nf = max(2, int(dur * FPS))
    for pct in (0, 0.25, 0.5, 0.75, 0.9):
        sample_positions.append(idx + int(nf * pct))
    idx += nf

sample_positions = sorted(set(sample_positions))
print(f"  Sampling {len(sample_positions)} frames...", flush=True)

contact = Image.new("RGB", (1600, 6000), BG)
y_off = 10
for si, spos in enumerate(sample_positions[:24]):
    if spos >= total_frames:
        break
    src = os.path.join(FRAMES_DIR, f"frame_{spos:06d}.ppm")
    if not os.path.exists(src):
        continue
    img = Image.open(src)
    thumb = img.resize((300, 533))
    col, row = si % 5, si // 5
    x, y = 10 + col * 310, 10 + row * 550
    contact.paste(thumb, (x, y))
    cd = ImageDraw.Draw(contact)
    cd.text((x, y + 540), f"f{spos} ({spos * 100 // total_frames}%)", font=font(20), fill=(150, 150, 150))
    y_off = y + 560

contact_path = os.path.join(contact_dir, "contact_sheet.jpg")
contact = contact.crop((0, 0, 1600, y_off + 10))
contact.save(contact_path, "JPEG", quality=85)
print(f"  Contact sheet: {contact_path}", flush=True)

# ── Step 6: Registry ──
print("\n=== Step 6: Registry ===", flush=True)
registry = []
if os.path.exists(REGISTRY):
    try:
        data = json.load(open(REGISTRY))
        registry = data if isinstance(data, list) else [data]
    except Exception:
        registry = []

episode_record = {
    "episode_id": f"ep-{datetime.now().strftime('%Y-%m-%d')}-{EPISODE['episode_id_slug']}",
    "date": datetime.now().strftime('%Y-%m-%d'),
    "word_pair": EPISODE["word_pair"],
    "level": EPISODE["level"].replace("LEVEL ", ""),
    "format": "vertical_9_16",
    "duration_sec": round(final_dur),
    "voice": "edge-tts/en-US-GuyNeural",
    "voice_speed": 1.0,
    "brand": "vibte.com",
    "video_path": OUT_VIDEO,
}
registry.append(episode_record)
with open(REGISTRY, "w") as f:
    json.dump(registry, f, indent=2)
print(f"  Registry: {len(registry)} episodes saved", flush=True)

print(f"\n{'='*50}", flush=True)
print(f"  DONE — {OUT_VIDEO}", flush=True)
print(f"  Duration: {final_dur:.1f}s", flush=True)
print(f"  Contact sheet: {contact_path}", flush=True)
print(f"{'='*50}", flush=True)
