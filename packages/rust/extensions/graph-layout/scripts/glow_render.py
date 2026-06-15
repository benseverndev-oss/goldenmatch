#!/usr/bin/env python3
"""HDR additive-glow renderer for graph-layout --dump-bin output.

Turns the raw per-frame positions into a luminous nebula: gradient-colored edge
filaments + gaussian-splat star cores accumulated into a float HDR buffer, then
multi-scale bloom and an ACES filmic tonemap. Pure numpy (no scipy/PIL) -- the
blur is a cumsum box-blur (3 passes ~= gaussian), O(pixels) per scale.

  python glow_render.py galaxy.bin out_dir [--frame N] [--res 1440]
"""
import sys, os, struct, math
import numpy as np


def load(path):
    with open(path, "rb") as f:
        buf = f.read()
    n, m, w, h = struct.unpack_from("<IIII", buf, 0)
    off = 16
    colors = np.frombuffer(buf, "<u4", n, off).copy(); off += 4 * n
    radii = np.frombuffer(buf, "<f4", n, off).copy(); off += 4 * n
    edges = np.frombuffer(buf, "<u4", 2 * m, off).reshape(m, 2).copy(); off += 8 * m
    rest = (len(buf) - off) // (8 * n)
    pos = np.frombuffer(buf, "<f4", rest * n * 2, off).reshape(rest, n, 2).copy()
    return n, m, w, h, colors, radii, edges, pos


def palette(ncol):
    """Vibrant, well-separated community colors (golden-ratio hue walk)."""
    out = np.zeros((ncol, 3), np.float32)
    for i in range(ncol):
        hgt = (i * 0.61803398875) % 1.0
        # full saturation, full value -> neon under additive+bloom
        c = hsv(hgt, 0.85, 1.0)
        out[i] = c
    return out


def hsv(h, s, v):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def box_blur(img, r):
    if r < 1:
        return img
    k = 2 * r + 1
    out = img
    for _ in range(3):  # 3 box passes approximate a gaussian
        out = _box1d(_box1d(out, k, 0), k, 1)
    return out


def _box1d(img, k, axis):
    pad = k // 2
    c = np.cumsum(img, axis=axis)
    c = np.concatenate([np.zeros_like(np.take(c, [0], axis)), c], axis=axis)
    n = img.shape[axis]
    lo = np.clip(np.arange(n) - pad, 0, n)
    hi = np.clip(np.arange(n) + pad + 1, 0, n)
    s = np.take(c, hi, axis) - np.take(c, lo, axis)
    return s / k


def scatter_add(buf, ys, xs, vals):
    H, W, _ = buf.shape
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    yi, xi, vv = ys[ok].astype(np.intp), xs[ok].astype(np.intp), vals[ok]
    flat = (yi * W + xi)
    bf = buf.reshape(-1, 3)
    np.add.at(bf, flat, vv)


def aces(x):
    return np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0, 1)


def render(n, m, w, h, colors, radii, edges, pos, frame_idx, res,
           edge_t, e_src, e_dst, pal, fixed_bbox):
    P = pos[frame_idx]
    # Robust per-frame fit: percentile bbox so the condensing bulk always fills
    # the frame (a few stray outliers may clip — they're dim and it avoids the
    # whole galaxy shrinking to a dot when one node flies out).
    lo = np.percentile(P, 0.5, axis=0)
    hi = np.percentile(P, 99.5, axis=0)
    span = np.maximum(hi - lo, 1e-3)
    pad = 0.08
    s = min(res * (1 - 2 * pad) / span[0], res * (1 - 2 * pad) / span[1])
    ox = (res - span[0] * s) * 0.5 - lo[0] * s
    oy = (res - span[1] * s) * 0.5 - lo[1] * s
    scr = np.empty_like(P)
    scr[:, 0] = P[:, 0] * s + ox
    scr[:, 1] = P[:, 1] * s + oy

    hdr = np.zeros((res, res, 3), np.float32)

    # --- edges: gradient-colored filaments (samples along each edge) ---
    a = scr[e_src]; b = scr[e_dst]
    ca = pal[colors[e_src]]; cb = pal[colors[e_dst]]
    ex = a[:, 0:1] * (1 - edge_t) + b[:, 0:1] * edge_t  # (E, S)
    ey = a[:, 1:2] * (1 - edge_t) + b[:, 1:2] * edge_t  # (E, S)
    tt = edge_t[None, :, None]                          # (1, S, 1)
    ec = ca[:, None, :] * (1 - tt) + cb[:, None, :] * tt  # (E, S, 3)
    # Inter-community bridges are the cosmic web: few, so draw them far brighter
    # and pushed toward white so they read as glowing threads spanning the gaps;
    # intra-community edges stay faint and just thicken each island's body.
    inter = (colors[e_src] != colors[e_dst])
    ei = np.where(inter, 0.16, 0.013).astype(np.float32)[:, None, None]  # (E,1,1)
    ec = ec + inter[:, None, None] * 0.5 * (1.0 - ec)  # whiten bridges
    scatter_add(hdr, ey.reshape(-1), ex.reshape(-1),
                (ec * ei).reshape(-1, 3))

    # --- nodes: soft orbs (intensity ~ radius^2 so big entities blaze) ---
    nb = pal[colors] * (0.6 + 0.5 * (radii / radii.max())[:, None] ** 2)
    NI = 2.6
    # 3x3 footprint so cores are orbs, not single-pixel grain
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            wgt = 1.0 if (dx == 0 and dy == 0) else 0.5
            scatter_add(hdr, scr[:, 1] + dy, scr[:, 0] + dx, nb * NI * wgt)

    # --- bloom: threshold + multi-scale box-blur, added back ---
    bright = np.maximum(hdr - 0.5, 0.0)
    bloom = np.zeros_like(hdr)
    for r, wgt in [(2, 0.7), (6, 0.8), (18, 0.9), (52, 0.7)]:
        bloom += box_blur(bright, r) * wgt
    hdr = hdr + bloom * 1.6

    # --- tonemap + gamma ---
    ldr = aces(hdr * 1.1)
    ldr = np.clip(ldr, 0, 1) ** (1 / 2.2)
    return (ldr * 255 + 0.5).astype(np.uint8)


def main():
    path, out = sys.argv[1], sys.argv[2]
    frame = None
    res = 1440
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--frame":
            frame = int(args[i + 1])
        if a == "--res":
            res = int(args[i + 1])
    n, m, w, h, colors, radii, edges, pos = load(path)
    ncol = int(colors.max()) + 1
    pal = palette(ncol)
    # per-edge sample params (static): samples ~ proportional to a nominal length
    Ssamp = 14
    edge_t = np.linspace(0.0, 1.0, Ssamp, dtype=np.float32)
    e_src, e_dst = edges[:, 0], edges[:, 1]
    # fixed bbox from the final (settled) frame so the galaxy condenses into frame
    fin = pos[-1]
    fixed_bbox = (fin.min(0), fin.max(0))

    os.makedirs(out, exist_ok=True)
    frames = [frame] if frame is not None else range(pos.shape[0])
    for fi in frames:
        img = render(n, m, w, h, colors, radii, edges, pos, fi, res,
                     edge_t, e_src, e_dst, pal, fixed_bbox)
        write_ppm(os.path.join(out, f"glow_{fi:05d}.ppm"), img)
    print(f"rendered {len(list(frames)) if frame is None else 1} frame(s) -> {out}")


def write_ppm(path, img):
    h, w, _ = img.shape
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        f.write(img.tobytes())


if __name__ == "__main__":
    main()
