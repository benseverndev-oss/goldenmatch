#!/usr/bin/env python3
"""README hero: messy records resolve into glowing golden records (the ledger).

Brief: a developer's first 3 seconds on the README. Make them feel "this turns
messy duplicate records into one clean golden record" -- on-brand gold, clean,
honest, with a bit of wow. A short seamless loop: a table of messy duplicate rows
collapses into a few GOLDEN records that glow and catch the light (bloom + a
specular shimmer sweep), with a live count.

Pure PIL/numpy (renders at 2x, downsamples for crisp type). Curated data, hand-
picked so the duplicates are obvious at a glance.

  python examples/hero_ledger.py                  # dark  -> hero_ledger.{mp4,gif,png}
  python examples/hero_ledger.py --theme light    # light -> hero_ledger_light.*
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONTS = Path("/mnt/skills/examples/canvas-design/canvas-fonts")
W, H, SS = 1200, 630, 2

GOLD = (212, 160, 23); GOLD_HI = (232, 185, 35); CREAM = (255, 248, 225)

THEMES = {
    "dark": dict(
        bg_top=(20, 17, 10), bg_bot=(11, 9, 6), glow=(46, (44, 34, 14)),
        ink=(233, 227, 211), dim=(150, 140, 120), faint=(95, 88, 72),
        rule=(80, 72, 54), row=(28, 24, 15), on_gold=(38, 28, 6),
        sub_gold=(92, 70, 18), bloom=0.5, shimmer=0.42, brand=(255, 255, 255)),
    "light": dict(
        bg_top=(247, 243, 234), bg_bot=(238, 231, 216), glow=(26, (255, 246, 222)),
        ink=(42, 36, 26), dim=(130, 118, 96), faint=(168, 156, 132),
        rule=(214, 203, 180), row=(239, 233, 220), on_gold=(46, 33, 6),
        sub_gold=(92, 68, 14), bloom=0.4, shimmer=0.55, brand=(34, 28, 18)),
}

GROUPS = [
    [("Jonathan Smith", "jon.smith@acme.io", "New York"),
     ("Jon Smith",      "jsmith@acme.io",    "New York"),
     ("J. Smith",       "jon.smith@acme.io", "NYC")],
    [("Maria Garcia",   "maria.g@globex.com", "Austin"),
     ("Maria  Garcia",  "maria.g@globex.com", "Austin")],
    [("Robert Chen",    "rchen@initech.com",  "Seattle"),
     ("Bob Chen",       "rchen@initech.com",  "Seattle"),
     ("Robert Chen",    "r.chen@initech.com", "Seatle")],
    [("Linda Park",     "lpark@umbrella.co",  "Denver")],
]


def font(name, sz):
    return ImageFont.truetype(str(FONTS / name), sz * SS)


def lerp(a, b, t): return a + (b - a) * t
def mix(c1, c2, t): return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))
def ease(t): return t * t * (3 - 2 * t)
def clamp01(x): return max(0.0, min(1.0, x))


ROWS = []
gy = 0
for gi, g in enumerate(GROUPS):
    surv = len(ROWS)
    for ri, rec in enumerate(g):
        ROWS.append({"rec": rec, "group": gi, "survivor": (ri == 0),
                     "surv_idx": surv, "golden_row": gy if ri == 0 else None})
    gy += 1
N_IN = len(ROWS)
N_OUT = len(GROUPS)


def draw_disc(d, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD)
    d.ellipse([cx - r * 0.42, cy - r * 0.42, cx + r * 0.42, cy + r * 0.42], fill=CREAM)


def background(TH):
    arr = np.zeros((H * SS, W * SS, 3), np.float32)
    col = np.linspace(0, 1, H * SS)[:, None]
    for i in range(3):
        arr[:, :, i] = lerp(TH["bg_top"][i], TH["bg_bot"][i], col)
    img = Image.fromarray(arr.astype(np.uint8))
    amt, tint = TH["glow"]
    glow = Image.new("L", (W * SS, H * SS), 0)
    ImageDraw.Draw(glow).ellipse(
        [int(-0.1 * W * SS), int(-0.3 * H * SS), int(0.7 * W * SS), int(0.7 * H * SS)], fill=amt)
    glow = glow.filter(ImageFilter.GaussianBlur(120 * SS // 2))
    return Image.composite(Image.new("RGB", img.size, tint), img, glow)


def render(t, count, TH, sweep=-1.0, flash=0.0):
    s = SS
    bg = background(TH).convert("RGBA")
    img = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    gmask = Image.new("L", bg.size, 0)            # gold coverage (for bloom/shimmer)
    gd = ImageDraw.Draw(gmask)

    f_brand = font("InstrumentSans-Bold.ttf", 23)
    f_tag = font("InstrumentSans-Regular.ttf", 14)
    f_count = font("InstrumentSans-Bold.ttf", 30)
    f_clbl = font("InstrumentSans-Regular.ttf", 14)
    f_mono = font("JetBrainsMono-Regular.ttf", 15)
    f_monb = font("JetBrainsMono-Bold.ttf", 15)
    f_hd = font("JetBrainsMono-Bold.ttf", 12)
    f_tag2 = font("InstrumentSans-Regular.ttf", 15)
    INK, DIM, FAINT = TH["ink"], TH["dim"], TH["faint"]

    M = 64 * s
    draw_disc(d, M + 13 * s, 50 * s, 13 * s)
    d.text((M + 34 * s, 38 * s), "GoldenMatch", font=f_brand, fill=TH["brand"])
    d.text((M + 35 * s, 66 * s), "zero-config entity resolution", font=f_tag, fill=DIM)

    et = ease(t)
    pill_y = 44 * s
    rtxt = f"{N_IN} records"
    d.text((W * s - M - _tw(d, rtxt, f_clbl) - 250 * s, pill_y + 7 * s), rtxt, font=f_clbl, fill=DIM)
    ca = int(round(255 * et))
    ax = W * s - M - 232 * s
    d.text((ax, pill_y + 5 * s), "→", font=f_count, fill=(*GOLD, ca))
    chip_w, chip_h = 196 * s, 46 * s
    cx0 = W * s - M - chip_w
    _rrect(d, cx0, pill_y - 3 * s, cx0 + chip_w, pill_y - 3 * s + chip_h, 10 * s,
           fill=(*GOLD, int(0.85 * ca)), outline=(*GOLD_HI, ca), w=max(1, s))
    gd.rounded_rectangle([cx0, pill_y - 3 * s, cx0 + chip_w, pill_y - 3 * s + chip_h],
                         radius=10 * s, fill=int(0.85 * ca))
    d.text((cx0 + 16 * s, pill_y + 5 * s), str(count), font=f_count, fill=(*CREAM, ca))
    d.text((cx0 + 52 * s, pill_y + 14 * s), "golden records", font=f_clbl, fill=(*CREAM, ca))

    tx = M; tw = W * s - 2 * M
    top = 116 * s; rh = 44 * s
    cols = [tx + 22 * s, tx + 300 * s, tx + 620 * s]
    d.text((cols[0], top), "NAME", font=f_hd, fill=FAINT)
    d.text((cols[1], top), "EMAIL", font=f_hd, fill=FAINT)
    d.text((cols[2], top), "CITY", font=f_hd, fill=FAINT)
    d.line([tx, top + 24 * s, tx + tw, top + 24 * s], fill=(*TH["rule"], 255), width=max(1, s))
    body = top + 38 * s

    order = sorted(range(N_IN), key=lambda i: ROWS[i]["survivor"])
    for i in order:
        r = ROWS[i]
        messy_y = body + i * rh
        if r["survivor"]:
            golden_y = body + r["golden_row"] * rh
            y = lerp(messy_y, golden_y, et)
            _row(d, gd, tx, y, tw, rh, r["rec"], cols, f_mono, f_monb, TH, gold=et, alpha=1.0)
        else:
            sy = body + ROWS[r["surv_idx"]]["golden_row"] * rh
            y = lerp(messy_y, sy, ease(clamp01(t * 1.15)))
            a = clamp01(1.0 - ease(clamp01(t * 1.4)))
            if a > 0.02:
                _row(d, gd, tx, y, tw, rh, r["rec"], cols, f_mono, f_monb, TH, gold=0.0, alpha=a)

    ta = clamp01((t - 0.58) / 0.32)
    if ta > 0:
        d.text((tx + 2 * s, body + N_OUT * rh + 30 * s),
               "Messy duplicates in. One golden record each out.", font=f_tag2,
               fill=(*INK, int(round(255 * ease(ta)))))

    # ---- composite + light effects (bloom, shimmer, flash) ----
    base = np.asarray(Image.alpha_composite(bg, img).convert("RGB"), np.float32)
    gm = np.asarray(gmask, np.float32) / 255.0
    if gm.max() > 0:
        bl = np.asarray(Image.fromarray((gm * 255).astype(np.uint8))
                        .filter(ImageFilter.GaussianBlur(9 * s)), np.float32) / 255.0
        halo = np.array(GOLD, np.float32) * TH["bloom"]      # warm amber glow, not neon
        base += bl[:, :, None] * halo[None, None, :]
        if sweep >= 0:                              # specular sweep across gold rows
            Hs, Ws = gm.shape
            xx = np.arange(Ws)[None, :]; yy = np.arange(Hs)[:, None]
            band = 70 * s
            cx = sweep * (Ws + 2 * band) - band + 0.35 * (yy - Hs / 2)   # diagonal
            spec = np.exp(-((xx - cx) / band) ** 2) * gm
            base += spec[:, :, None] * (np.array(CREAM, np.float32) * TH["shimmer"])[None, None, :]
        if flash > 0:                               # merge pop
            base += gm[:, :, None] * (np.array(CREAM, np.float32) * (0.6 * flash))[None, None, :]
    out = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).resize((W, H), Image.LANCZOS)
    return np.asarray(out)


def _tw(d, txt, f): return d.textlength(txt, font=f)


def _rrect(d, x0, y0, x1, y1, rad, fill=None, outline=None, w=1):
    d.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=fill, outline=outline, width=w)


def _row(d, gd, tx, y, tw, rh, rec, cols, f_mono, f_monb, TH, gold, alpha):
    s = SS
    if gold > 0.02:
        bg = mix(TH["row"], GOLD, gold)
        _rrect(d, tx, y, tx + tw, y + rh - 8 * s, 9 * s, fill=(bg[0], bg[1], bg[2], int(255 * alpha)))
        gd.rounded_rectangle([tx, y, tx + tw, y + rh - 8 * s], radius=9 * s, fill=int(255 * alpha * gold))
        tag_a = clamp01((gold - 0.66) / 0.34)
        if tag_a > 0:
            tagx = tx + tw - 78 * s
            _rrect(d, tagx, y + 11 * s, tx + tw - 14 * s, y + rh - 19 * s, 8 * s, fill=(255, 248, 225, int(235 * tag_a)))
            d.text((tagx + 10 * s, y + 12 * s), "golden", font=f_monb, fill=(58, 42, 8, int(255 * tag_a)))
        ink = mix(TH["ink"], TH["on_gold"], gold); sub = mix(TH["dim"], TH["sub_gold"], gold)
    else:
        _rrect(d, tx, y, tx + tw, y + rh - 8 * s, 9 * s, fill=(*TH["row"], int(200 * alpha)))
        ink, sub = TH["ink"], TH["dim"]
    ty = y + 9 * s; A = int(255 * alpha)
    d.text((cols[0], ty), rec[0], font=f_monb, fill=(*ink, A))
    d.text((cols[1], ty), rec[1], font=f_mono, fill=(*sub, A))
    d.text((cols[2], ty), rec[2], font=f_mono, fill=(*sub, A))


def main():
    theme = "dark"; out = "hero_ledger"
    a = sys.argv[1:]
    for i, t in enumerate(a):
        if t == "--theme":
            theme = a[i + 1]
            if theme == "light":
                out = "hero_ledger_light"
    TH = THEMES[theme]
    fps = 30
    A, B, C, D = 26, 80, 76, 30
    frames = []
    def count_at(t): return int(round(lerp(N_IN, N_OUT, ease(clamp01(t * 1.2)))))
    for k in range(A):
        frames.append(render(0.0, N_IN, TH))
    for k in range(B):
        t = (k + 1) / B
        fl = clamp01((t - 0.86) / 0.14) ** 2 * 0.5    # brief pop as the merge locks in
        frames.append(render(t, count_at(t), TH, flash=fl))
    for k in range(C):
        p = k / C
        sw = ease(clamp01((p - 0.12) / 0.4)) if 0.12 <= p < 0.52 else -1.0   # shimmer sweep
        fl = (0.5 * max(0.0, 1 - p / 0.09)) if p < 0.09 else 0.0
        frames.append(render(1.0, N_OUT, TH, sweep=sw, flash=fl))
    golden = frames[-1].astype(np.float32); messy = frames[A].astype(np.float32)
    half = D // 2
    for k in range(half):
        frames.append((golden * (1 - ease((k + 1) / half))).astype(np.uint8))
    for k in range(half):
        frames.append((messy * ease((k + 1) / half)).astype(np.uint8))
    print(f"[{theme}] rendered {len(frames)} frames ({len(frames)/fps:.1f}s loop)")

    import imageio.v2 as imageio
    imageio.mimsave(f"/tmp/{out}.mp4", frames, fps=fps, codec="libx264",
                    quality=9, macro_block_size=8, ffmpeg_log_level="error")
    imageio.mimsave(f"/tmp/{out}.gif", frames[::2], duration=1000 / (fps / 2), loop=0)
    Image.fromarray(frames[A + B + 14]).save(f"/tmp/{out}_poster.png")
    print(f"wrote /tmp/{out}.mp4, .gif, _poster.png")


if __name__ == "__main__":
    main()
