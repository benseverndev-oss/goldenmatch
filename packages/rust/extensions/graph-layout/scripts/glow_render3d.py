#!/usr/bin/env python3
"""3D HDR glow renderer for graph-layout --dump-bin output.

Renders the dumped graph as a seamless 8s "big-bang" loop: messy multicolor
noise -> implodes to a white-hot point -> bursts out into 3D community clusters
-> slow orbit sweeping both horizontal and vertical -> dissolves back to the same
noise. Seamless because end-state == start-state with smoothstep (zero velocity)
at every boundary incl. the loop seam, and a periodic camera path.

Look passes (pure numpy, no scipy/PIL):
- dense community orbs (power-law radial packing -> bright cores, soft halos)
- glowing inter-community bridges (whitened, high-intensity threads = cosmic web)
- motion-blur trails (temporal supersampling during implosion/explosion) + a
  push-in dolly on the burst
- atmosphere: faint static starfield, vignette, gentle teal/orange color grade

  python glow_render3d.py galaxy.bin out_dir [--res 1080] [--frames 240] [--only K]
"""
import sys, os, struct
import numpy as np

RNG = np.random.default_rng(7)

FOC0 = 0.47        # base focal fraction
D0 = 3.45          # base camera distance
NODE_GAIN = 1.0    # per-node brightness multiplier (raise for sparse real graphs)


def load(path):
    with open(path, "rb") as f:
        buf = f.read()
    n, m, w, h = struct.unpack_from("<IIII", buf, 0)
    off = 16
    colors = np.frombuffer(buf, "<u4", n, off).copy(); off += 4 * n
    radii = np.frombuffer(buf, "<f4", n, off).copy(); off += 4 * n
    edges = np.frombuffer(buf, "<u4", 2 * m, off).reshape(m, 2).copy()
    return n, m, colors, radii, edges


def hsv(h, s, v):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def palette(ncol):
    out = np.zeros((ncol, 3), np.float32)
    for i in range(ncol):
        out[i] = hsv((i * 0.61803398875) % 1.0, 0.82, 1.0)
    return out


def build_3d(n, colors, edges):
    """Two-level 3D layout: community centroids by force sim, nodes as dense
    3D orbs (power-law radius -> concentrated core + sparse halo)."""
    ncol = int(colors.max()) + 1
    W = np.zeros((ncol, ncol), np.float64)
    cs, cd = colors[edges[:, 0]], colors[edges[:, 1]]
    inter = cs != cd
    np.add.at(W, (cs[inter], cd[inter]), 1.0)
    W = W + W.T
    cnt = np.bincount(colors, minlength=ncol).astype(np.float64)

    C = RNG.standard_normal((ncol, 3)) * 0.6
    for _ in range(600):
        d = C[:, None, :] - C[None, :, :]
        dist2 = (d * d).sum(-1) + 1e-3
        rep = (d / dist2[:, :, None] ** 1.5).sum(1) * 0.04
        att = (-(d) * W[:, :, None]).sum(1) * 0.0008
        C += rep + att - C * 0.012
    C -= C.mean(0)
    C /= np.abs(C).max() + 1e-6

    rmax = 0.17 * (cnt / cnt.mean()) ** (1.0 / 3.0)        # per-community orb size
    dirs = RNG.standard_normal((n, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True) + 1e-9
    rad = rmax[colors] * RNG.random(n) ** 1.7              # power>1 -> dense core
    P = C[colors] + dirs * rad[:, None]
    P -= P.mean(0)
    P /= np.percentile(np.linalg.norm(P, axis=1), 99) + 1e-6
    return P.astype(np.float32)


def ball_noise(n, radius):
    d = RNG.standard_normal((n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    r = radius * RNG.random(n) ** (1.0 / 3.0)
    return (d * r[:, None]).astype(np.float32)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# --- loop choreography -------------------------------------------------------
sB, sC, sD, sE = 0.07, 0.20, 0.32, 0.86


def morph(p, N, Opt, C):
    if p < sB:
        return N
    if p < sC:                                   # implode: noise -> point
        s = smoothstep(sB, sC, p)
        return N * (1 - s) + Opt * s
    if p < sD:                                   # explode: point -> clusters
        t = np.clip((p - sC) / (sD - sC), 0.0, 1.0)
        s = 1.0 - (1.0 - t) ** 1.9               # ease-OUT burst
        return Opt * (1 - s) + C * s
    if p < sE:
        return C
    s = smoothstep(sE, 1.0, p)                   # dissolve clusters -> noise
    return C * (1 - s) + N * s


def camera(p):
    az = 2 * np.pi * p                                   # one horizontal turn
    tilt = np.radians(6.0 + 24.0 * np.sin(2 * np.pi * p))  # vertical sweep
    # push-in dolly: ease closer across the explosion, slowly pull back in orbit
    if p < sC:
        D = D0
    elif p < sD:
        D = D0 * (1 - smoothstep(sC, sD, p)) + 2.85 * smoothstep(sC, sD, p)
    elif p < sE:
        D = 2.85 * (1 - smoothstep(sD, sE, p)) + D0 * smoothstep(sD, sE, p)
    else:
        D = D0
    return az, tilt, D


def shutter(p):
    """Trailing motion-blur length (in frames). Zero during the slow orbit."""
    if sB <= p < sC:
        return 1.6                               # implosion smear
    if sC <= p < sD + 0.02:
        return 3.0                               # explosion streaks
    return 0.0


# --- rasterization -----------------------------------------------------------
def box_blur(img, r):
    if r < 1:
        return img
    k = 2 * r + 1
    out = img
    for _ in range(3):
        out = _box1d(_box1d(out, k, 0), k, 1)
    return out


def _box1d(img, k, axis):
    pad = k // 2
    c = np.cumsum(img, axis=axis)
    c = np.concatenate([np.zeros_like(np.take(c, [0], axis)), c], axis=axis)
    nn = img.shape[axis]
    lo = np.clip(np.arange(nn) - pad, 0, nn)
    hi = np.clip(np.arange(nn) + pad + 1, 0, nn)
    return (np.take(c, hi, axis) - np.take(c, lo, axis)) / k


def scatter_add(buf, ys, xs, vals):
    H, W, _ = buf.shape
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H) & np.isfinite(xs) & np.isfinite(ys)
    flat = (ys[ok].astype(np.intp) * W + xs[ok].astype(np.intp))
    np.add.at(buf.reshape(-1, 3), flat, vals[ok])


def aces(x):
    return np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0, 1)


def project(pts, D, res):
    focal = FOC0 * res * D0
    z = pts[..., 2]
    dcam = np.maximum(D - z, 0.05)
    f = focal / dcam
    c = res * 0.5
    sx = c + pts[..., 0] * f
    sy = c - pts[..., 1] * f
    depth = np.clip((f / (FOC0 * res)) ** 1.7, 0.0, 4.0)     # near brighter/bigger
    fog = np.clip(1.0 - (0.85 - z) * 0.5, 0.2, 1.0)          # far recedes
    return sx, sy, depth * fog


def render_into(hdr, P, colors, radii, pal, edge_t, e_src, e_dst, inter_e,
                az, tilt, D, res, weight):
    ca, sa = np.cos(az), np.sin(az)
    ct, st = np.cos(tilt), np.sin(tilt)
    x = P[:, 0] * ca + P[:, 2] * sa
    z0 = -P[:, 0] * sa + P[:, 2] * ca
    y = P[:, 1] * ct - z0 * st
    z = P[:, 1] * st + z0 * ct
    R = np.stack([x, y, z], 1)

    # edges -- intra thicken orbs, inter are whitened high-intensity bridges
    a = R[e_src]; b = R[e_dst]
    ep = a[:, None, :] * (1 - edge_t)[None, :, None] + b[:, None, :] * edge_t[None, :, None]
    sx, sy, br = project(ep, D, res)
    ca_ = pal[colors[e_src]]; cb_ = pal[colors[e_dst]]
    tt = edge_t[None, :, None]
    ec = ca_[:, None, :] * (1 - tt) + cb_[:, None, :] * tt
    ec = ec + inter_e[:, None, None] * 0.6 * (1.0 - ec)      # whiten bridges
    ei = np.where(inter_e, 0.30, 0.006)[:, None]             # bright web threads
    vals = ec * (br * ei)[:, :, None] * weight
    scatter_add(hdr, sy.reshape(-1), sx.reshape(-1), vals.reshape(-1, 3))

    # nodes -- soft orbs
    nsx, nsy, nbr = project(R, D, res)
    nb = pal[colors] * (0.6 + 0.5 * (radii / radii.max())[:, None] ** 2)
    base = nb * (2.0 * NODE_GAIN * nbr[:, None] * weight)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            wgt = 1.0 if (dx == 0 and dy == 0) else 0.4
            scatter_add(hdr, nsy + dy, nsx + dx, base * wgt)


def make_starfield(res):
    layer = np.zeros((res, res, 3), np.float32)
    ns = int(res * res / 1600)
    xs = RNG.integers(0, res, ns)
    ys = RNG.integers(0, res, ns)
    b = (RNG.random(ns) ** 3.5) * 0.32
    tint = np.array([0.8, 0.85, 1.0], np.float32)            # cool white
    scatter_add(layer, ys, xs, (b[:, None] * tint))
    return layer


def grade(ldr):
    lum = ldr.mean(2, keepdims=True)
    shadow = np.array([0.0, 0.012, 0.045], np.float32)       # cool shadows
    high = np.array([0.05, 0.018, 0.0], np.float32)          # warm highlights
    ldr = ldr + shadow * (1 - lum) + high * lum
    return np.clip(ldr, 0, 1)


def main():
    path, out = sys.argv[1], sys.argv[2]
    res, M, only = 1080, 240, None
    a = sys.argv[3:]
    for i, t in enumerate(a):
        if t == "--res": res = int(a[i + 1])
        if t == "--frames": M = int(a[i + 1])
        if t == "--only": only = int(a[i + 1])
        if t == "--node-gain":                       # brighten sparse real graphs
            global NODE_GAIN
            NODE_GAIN = float(a[i + 1])
    n, m, colors, radii, edges = load(path)
    pal = palette(int(colors.max()) + 1)
    C = build_3d(n, colors, edges)
    N = ball_noise(n, 0.95)
    Opt = (RNG.standard_normal((n, 3)) * 0.02).astype(np.float32)
    edge_t = np.linspace(0, 1, 8, dtype=np.float32)
    e_src, e_dst = edges[:, 0], edges[:, 1]
    inter_e = (colors[e_src] != colors[e_dst]).astype(np.float32)

    stars = make_starfield(res)
    yy, xx = np.mgrid[0:res, 0:res]
    rr = np.sqrt((xx - res / 2) ** 2 + (yy - res / 2) ** 2) / (res * 0.5)
    vignette = np.clip(1.0 - 0.42 * rr ** 2.3, 0.0, 1.0)[:, :, None].astype(np.float32)

    os.makedirs(out, exist_ok=True)
    idxs = [only] if only is not None else range(M)
    for k in idxs:
        p = k / M
        sh = shutter(p)
        K = 7 if sh > 0 else 1
        hdr = stars.copy()
        wsum = 0.0
        for j in range(K):
            pj = p - (j / max(K, 1)) * (sh / M)
            wj = 1.0 - 0.85 * (j / max(K, 1))            # comet-tail decay
            P = morph(pj, N, Opt, C)
            az, tilt, D = camera(pj if sh > 0 else p)
            render_into(hdr, P, colors, radii, pal, edge_t, e_src, e_dst,
                        inter_e, az, tilt, D, res, wj)
            wsum += wj
        hdr /= wsum

        bright = np.maximum(hdr - 0.6, 0.0)
        bloom = np.zeros_like(hdr)
        for r, wgt in [(3, 0.55), (10, 0.6), (30, 0.55)]:
            bloom += box_blur(bright, r) * wgt
        hdr = hdr + bloom * 1.05

        ldr = aces(hdr) ** (1 / 2.2)
        ldr = grade(ldr) * vignette
        img = (np.clip(ldr, 0, 1) * 255 + 0.5).astype(np.uint8)
        write_ppm(os.path.join(out, f"orbit_{k:05d}.ppm"), img)
    print(f"rendered {len(list(idxs)) if only is None else 1} frame(s) -> {out}")


def write_ppm(path, img):
    h, w, _ = img.shape
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        f.write(img.tobytes())


if __name__ == "__main__":
    main()
