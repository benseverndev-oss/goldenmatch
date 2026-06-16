#!/usr/bin/env python3
"""README hero: messy records resolve into golden records (the ledger).

Brief: a developer's first 3 seconds on the README. Make them feel "this turns
messy duplicate records into one clean golden record" -- on-brand gold, clean,
honest, no chaos. A short seamless loop: a table of messy duplicate rows
collapses into a few glowing GOLDEN records, with a live count.

Pure PIL/numpy (renders at 2x, downsamples for crisp type). Curated data, hand-
picked so the duplicates are obvious at a glance.

  python examples/hero_ledger.py             # -> hero_ledger.mp4 + .gif + poster.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = Path("/mnt/skills/examples/canvas-design/canvas-fonts")
W, H, SS = 1200, 630, 2                      # output size, supersample factor

# brand
BG_TOP = (20, 17, 10); BG_BOT = (11, 9, 6)
INK = (233, 227, 211); DIM = (150, 140, 120); FAINT = (95, 88, 72)
GOLD = (212, 160, 23); GOLD_HI = (232, 185, 35); CREAM = (255, 248, 225)
ROW_A = (28, 24, 15); ROW_B = (23, 20, 12)
ON_GOLD = (38, 28, 6)

# curated: groups of duplicates; first row of each group is the canonical "golden"
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


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


def ease(t):
    return t * t * (3 - 2 * t)


# build flat row model with messy + golden target positions
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


def background(s):
    arr = np.zeros((H * SS, W * SS, 3), np.float32)
    for y in range(H * SS):
        f = y / (H * SS)
        arr[y, :] = [lerp(BG_TOP[i], BG_BOT[i], f) for i in range(3)]
    img = Image.fromarray(arr.astype(np.uint8))
    # faint warm radial glow, upper-left
    glow = Image.new("L", (W * SS, H * SS), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([int(-0.1 * W * SS), int(-0.3 * H * SS), int(0.7 * W * SS), int(0.7 * H * SS)], fill=46)
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(120 * SS // 2))
    img = Image.composite(Image.new("RGB", img.size, (44, 34, 14)), img, glow)
    return img


def render(t, count, fade_messy=0.0):
    """t in [0,1]: 0 = messy, 1 = golden. Returns an (H,W,3) uint8 array."""
    s = SS
    bg = background(s).convert("RGBA")
    img = Image.new("RGBA", bg.size, (0, 0, 0, 0))   # draw on transparent layer
    d = ImageDraw.Draw(img)                           # so alpha composites correctly
    f_brand = font("InstrumentSans-Bold.ttf", 23)
    f_tag = font("InstrumentSans-Regular.ttf", 14)
    f_count = font("InstrumentSans-Bold.ttf", 30)
    f_clbl = font("InstrumentSans-Regular.ttf", 14)
    f_mono = font("JetBrainsMono-Regular.ttf", 15)
    f_monb = font("JetBrainsMono-Bold.ttf", 15)
    f_hd = font("JetBrainsMono-Bold.ttf", 12)
    f_tag2 = font("InstrumentSans-Regular.ttf", 15)

    M = 64 * s                                   # outer margin
    # header: logo + wordmark
    draw_disc(d, M + 13 * s, 50 * s, 13 * s)
    d.text((M + 34 * s, 38 * s), "GoldenMatch", font=f_brand, fill=INK)
    d.text((M + 35 * s, 66 * s), "zero-config entity resolution", font=f_tag, fill=DIM)

    # count pill (top right): "9 records  ->  N golden records"
    et = ease(t)
    pill_y = 44 * s
    rtxt = f"{N_IN} records"
    d.text((W * s - M - _tw(d, rtxt, f_clbl) - 250 * s, pill_y + 7 * s), rtxt, font=f_clbl, fill=DIM)
    ca = int(round(255 * et))                    # chip fades in only as it resolves
    ax = W * s - M - 232 * s
    d.text((ax, pill_y + 5 * s), "→", font=f_count, fill=(*GOLD, ca))
    chip_w, chip_h = 196 * s, 46 * s
    cx0 = W * s - M - chip_w
    _rrect(d, cx0, pill_y - 3 * s, cx0 + chip_w, pill_y - 3 * s + chip_h, 10 * s,
           fill=(*GOLD, int(0.85 * ca)), outline=(*GOLD_HI, ca), w=max(1, s))
    d.text((cx0 + 16 * s, pill_y + 5 * s), str(count), font=f_count, fill=(*CREAM, ca))
    d.text((cx0 + 52 * s, pill_y + 14 * s), "golden records", font=f_clbl, fill=(*CREAM, ca))

    # table geometry
    tx = M
    tw = W * s - 2 * M
    top = 116 * s
    rh = 44 * s
    cols = [tx + 22 * s, tx + 300 * s, tx + 620 * s]   # name, email, city
    # header row
    d.text((cols[0], top), "NAME", font=f_hd, fill=FAINT)
    d.text((cols[1], top), "EMAIL", font=f_hd, fill=FAINT)
    d.text((cols[2], top), "CITY", font=f_hd, fill=FAINT)
    d.line([tx, top + 24 * s, tx + tw, top + 24 * s], fill=(80, 72, 54, 255), width=max(1, s))
    body = top + 38 * s

    # draw rows back-to-front: dupes first (so survivors overlay), then survivors
    order = sorted(range(N_IN), key=lambda i: ROWS[i]["survivor"])
    for i in order:
        r = ROWS[i]
        messy_y = body + i * rh
        if r["survivor"]:
            golden_y = body + r["golden_row"] * rh
            y = lerp(messy_y, golden_y, et)
            _row(d, tx, y, tw, rh, r["rec"], cols, f_mono, f_monb, gold=et, alpha=1.0)
        else:
            sy = body + ROWS[r["surv_idx"]]["golden_row"] * rh
            y = lerp(messy_y, sy, et)
            a = max(0.0, 1.0 - ease(min(1.0, t * 1.35)))
            if a > 0.02:
                _row(d, tx, y, tw, rh, r["rec"], cols, f_mono, f_monb, gold=0.0, alpha=a)

    # tagline bottom (fades in under the golden rows; hidden while messy)
    ta = max(0.0, min(1.0, (t - 0.58) / 0.32))
    if ta > 0:
        tline = "Messy duplicates in. One golden record each out."
        d.text((tx + 2 * s, body + N_OUT * rh + 30 * s), tline, font=f_tag2,
               fill=(*INK, int(round(255 * ease(ta)))))

    out = Image.alpha_composite(bg, img).convert("RGB").resize((W, H), Image.LANCZOS)
    return np.asarray(out)


def _tw(d, txt, f):
    return d.textlength(txt, font=f)


def _rrect(d, x0, y0, x1, y1, rad, fill=None, outline=None, w=1):
    d.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=fill, outline=outline, width=w)


def _row(d, tx, y, tw, rh, rec, cols, f_mono, f_monb, gold, alpha):
    s = SS
    pad = 6 * s
    if gold > 0.02:
        bg = mix(ROW_A, GOLD, gold)
        _rrect(d, tx, y, tx + tw, y + rh - 8 * s, 9 * s,
               fill=(bg[0], bg[1], bg[2], int(255 * alpha)))
        tag_a = max(0.0, min(1.0, (gold - 0.66) / 0.34))   # tag fades in last
        if tag_a > 0:
            tagx = tx + tw - 78 * s
            _rrect(d, tagx, y + 11 * s, tx + tw - 14 * s, y + rh - 19 * s, 8 * s,
                   fill=(255, 248, 225, int(235 * tag_a)))
            d.text((tagx + 10 * s, y + 12 * s), "golden", font=f_monb,
                   fill=(58, 42, 8, int(255 * tag_a)))
        ink = mix(INK, ON_GOLD, gold); sub = mix(DIM, (90, 68, 18), gold)
    else:
        _rrect(d, tx, y, tx + tw, y + rh - 8 * s, 9 * s, fill=(ROW_A[0], ROW_A[1], ROW_A[2], int(200 * alpha)))
        ink = (INK[0], INK[1], INK[2]); sub = (DIM[0], DIM[1], DIM[2])
    ty = y + 9 * s
    A = int(255 * alpha)
    d.text((cols[0], ty), rec[0], font=f_monb, fill=(ink[0], ink[1], ink[2], A))
    d.text((cols[1], ty), rec[1], font=f_mono, fill=(sub[0], sub[1], sub[2], A))
    d.text((cols[2], ty), rec[2], font=f_mono, fill=(sub[0], sub[1], sub[2], A))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "hero_ledger"
    fps = 30
    # timeline (frames): hold messy, resolve, hold golden, crossfade back
    A, B, C, D = 24, 80, 70, 30
    frames = []
    def count_at(t):
        return int(round(lerp(N_IN, N_OUT, ease(min(1.0, t * 1.2)))))
    for k in range(A):
        frames.append(render(0.0, N_IN))
    for k in range(B):
        t = (k + 1) / B
        frames.append(render(t, count_at(t)))
    for k in range(C):
        frames.append(render(1.0, N_OUT))
    golden = frames[-1].astype(np.float32); messy = frames[0].astype(np.float32)
    half = D // 2                                  # clean dip to black, then back
    for k in range(half):
        frames.append((golden * (1 - ease((k + 1) / half))).astype(np.uint8))
    for k in range(half):
        frames.append((messy * ease((k + 1) / half)).astype(np.uint8))
    print(f"rendered {len(frames)} frames ({len(frames)/fps:.1f}s loop)")

    import imageio.v2 as imageio
    imageio.mimsave(f"/tmp/{out}.mp4", frames, fps=fps, codec="libx264",
                    quality=9, macro_block_size=8, ffmpeg_log_level="error")
    imageio.mimsave(f"/tmp/{out}.gif", frames[::2], duration=1000 / (fps / 2), loop=0)
    Image.fromarray(frames[A + B + 10]).save(f"/tmp/{out}_poster.png")
    print(f"wrote /tmp/{out}.mp4, .gif, _poster.png")


if __name__ == "__main__":
    main()
