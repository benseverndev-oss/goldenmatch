#!/usr/bin/env python3
"""3D HDR glow renderer for graph-layout --dump-bin output.

Lifts the graph into a 3D volume and renders a slow camera orbit (turntable):
- community centroids laid out in 3D by repulsion + inter-community bridge
  attraction (a tiny force sim on the 40-node quotient graph),
- each community a 3D cloud of its member nodes,
- perspective projection with depth attenuation (near = brighter/larger, far
  fades into fog), additive accumulation, gentle multi-scale bloom, ACES tonemap.

Pure numpy. Output loops seamlessly (full 360 about the vertical axis).

  python glow_render3d.py galaxy.bin out_dir [--res 1080] [--frames 240] [--only K]
"""
import sys, os, struct
import numpy as np

RNG = np.random.default_rng(7)


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


def build_3d(n, colors, radii, edges):
    """Two-level 3D layout: community centroids by force sim, nodes as 3D clouds."""
    ncol = int(colors.max()) + 1
    # inter-community weight matrix
    W = np.zeros((ncol, ncol), np.float64)
    cs, cd = colors[edges[:, 0]], colors[edges[:, 1]]
    inter = cs != cd
    np.add.at(W, (cs[inter], cd[inter]), 1.0)
    W = W + W.T
    cnt = np.bincount(colors, minlength=ncol).astype(np.float64)

    # force sim on the quotient graph (cheap: ncol ~ 40)
    C = RNG.standard_normal((ncol, 3)) * 0.6
    for _ in range(600):
        d = C[:, None, :] - C[None, :, :]            # (c,c,3)
        dist2 = (d * d).sum(-1) + 1e-3
        rep = (d / dist2[:, :, None] ** 1.5).sum(1) * 0.04   # repulsion
        att = (-(d) * W[:, :, None]).sum(1) * 0.0008          # bridge attraction
        C += rep + att - C * 0.012                            # mild centering
    C -= C.mean(0)
    C /= np.abs(C).max() + 1e-6

    # place nodes: centroid + isotropic 3D gaussian, std ~ cube-root of size
    sigma = 0.082 * (cnt / cnt.mean()) ** (1.0 / 3.0)
    P = C[colors] + RNG.standard_normal((n, 3)) * sigma[colors][:, None]
    P -= P.mean(0)
    P /= np.percentile(np.linalg.norm(P, axis=1), 99) + 1e-6   # normalize scale
    return P


def ball_noise(n, radius):
    """Uniform points in a 3D ball (messy noise that fills the frame)."""
    d = RNG.standard_normal((n, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    r = radius * RNG.random(n) ** (1.0 / 3.0)
    return d * r[:, None]


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def morph(p, N, Opt, C):
    """Big-bang loop position at phase p in [0,1): noise -> point -> clusters
    -> (held while camera orbits) -> back to noise. Smoothstep at every segment
    so velocity is ~0 at each boundary (incl. the loop seam) => seamless."""
    sB, sC, sD, sE = 0.07, 0.20, 0.32, 0.86
    if p < sB:
        return N
    if p < sC:                       # implode: noise -> central point
        s = smoothstep(sB, sC, p)
        return N * (1 - s) + Opt * s
    if p < sD:                       # explode: point -> clusters (ease-OUT burst)
        t = np.clip((p - sC) / (sD - sC), 0.0, 1.0)
        s = 1.0 - (1.0 - t) ** 1.9   # fast out of the point, decelerate into place
        return Opt * (1 - s) + C * s
    if p < sE:                       # hold clusters (camera orbits)
        return C
    s = smoothstep(sE, 1.0, p)       # dissolve clusters -> noise (== start)
    return C * (1 - s) + N * s


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
    n = img.shape[axis]
    lo = np.clip(np.arange(n) - pad, 0, n)
    hi = np.clip(np.arange(n) + pad + 1, 0, n)
    return (np.take(c, hi, axis) - np.take(c, lo, axis)) / k


def scatter_add(buf, ys, xs, vals):
    H, W, _ = buf.shape
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H) & np.isfinite(xs) & np.isfinite(ys)
    flat = (ys[ok].astype(np.intp) * W + xs[ok].astype(np.intp))
    np.add.at(buf.reshape(-1, 3), flat, vals[ok])


def aces(x):
    return np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0, 1)


def frame(P, colors, radii, edges, pal, edge_t, e_src, e_dst, inter,
          az, tilt, res):
    # camera: azimuth (horizontal) orbit + tilt (vertical) sweep
    ca, sa = np.cos(az), np.sin(az)
    ct, st = np.cos(tilt), np.sin(tilt)
    # rotate about Y then tilt about X
    x = P[:, 0] * ca + P[:, 2] * sa
    z0 = -P[:, 0] * sa + P[:, 2] * ca
    y = P[:, 1] * ct - z0 * st
    z = P[:, 1] * st + z0 * ct
    R = np.stack([x, y, z], 1)

    D = 3.4               # camera distance along +z
    foc0 = 0.47
    focal = foc0 * res * D
    cx = cy = res * 0.5

    def project(pts):
        dcam = D - pts[..., 2]
        f = focal / np.maximum(dcam, 0.05)
        sx = cx + pts[..., 0] * f
        sy = cy - pts[..., 1] * f
        depth = np.clip((f / (foc0 * res)) ** 1.8, 0.0, 4.0)   # near brighter
        fog = np.clip(1.0 - (dcam - (D - 1.3)) * 0.55, 0.18, 1.0)
        return sx, sy, depth * fog

    hdr = np.zeros((res, res, 3), np.float32)

    # edges (sampled in 3D, projected per sample)
    a = R[e_src]; b = R[e_dst]
    ep = a[:, None, :] * (1 - edge_t)[None, :, None] + b[:, None, :] * edge_t[None, :, None]
    sx, sy, br = project(ep)                      # (E,S)
    ca_ = pal[colors[e_src]]; cb_ = pal[colors[e_dst]]
    tt = edge_t[None, :, None]
    ec = ca_[:, None, :] * (1 - tt) + cb_[:, None, :] * tt
    ec = ec + inter[:, None, None] * 0.35 * (1.0 - ec)        # whiten bridges
    ei = np.where(inter, 0.075, 0.006)[:, None]               # toned down, (E,1)
    vals = ec * (br * ei)[:, :, None]                         # (E,S,3)
    scatter_add(hdr, sy.reshape(-1), sx.reshape(-1), vals.reshape(-1, 3))

    # nodes (soft orbs, depth-scaled)
    nsx, nsy, nbr = project(R)
    nb = pal[colors] * (0.6 + 0.5 * (radii / radii.max())[:, None] ** 2)
    NI = 1.8                                                   # toned down
    base = nb * (NI * nbr[:, None])
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            wgt = 1.0 if (dx == 0 and dy == 0) else 0.4
            scatter_add(hdr, nsy + dy, nsx + dx, base * wgt)

    # gentle bloom
    bright = np.maximum(hdr - 0.65, 0.0)
    bloom = np.zeros_like(hdr)
    for r, wgt in [(3, 0.5), (10, 0.6), (30, 0.55)]:
        bloom += box_blur(bright, r) * wgt
    hdr = hdr + bloom * 1.0

    ldr = aces(hdr * 1.0)
    ldr = np.clip(ldr, 0, 1) ** (1 / 2.2)
    return (ldr * 255 + 0.5).astype(np.uint8)


def write_ppm(path, img):
    h, w, _ = img.shape
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        f.write(img.tobytes())


def main():
    path, out = sys.argv[1], sys.argv[2]
    res, M, only = 1080, 240, None
    a = sys.argv[3:]
    for i, t in enumerate(a):
        if t == "--res": res = int(a[i + 1])
        if t == "--frames": M = int(a[i + 1])
        if t == "--only": only = int(a[i + 1])
    n, m, colors, radii, edges = load(path)
    pal = palette(int(colors.max()) + 1)
    C = build_3d(n, colors, radii, edges)          # assembled 3D galaxy
    N = ball_noise(n, 0.95).astype(np.float32)     # messy start/end noise
    Opt = (RNG.standard_normal((n, 3)) * 0.02).astype(np.float32)  # dense point
    S = 6
    edge_t = np.linspace(0, 1, S, dtype=np.float32)
    e_src, e_dst = edges[:, 0], edges[:, 1]
    inter = (colors[e_src] != colors[e_dst]).astype(np.float32)

    os.makedirs(out, exist_ok=True)
    idxs = [only] if only is not None else range(M)
    for k in idxs:
        p = k / M
        P = morph(p, N, Opt, C).astype(np.float32)
        az = 2 * np.pi * p                                   # one horizontal turn
        tilt = np.radians(6.0 + 24.0 * np.sin(2 * np.pi * p))  # vertical sweep
        img = frame(P, colors, radii, edges, pal, edge_t, e_src, e_dst, inter,
                    az, tilt, res)
        write_ppm(os.path.join(out, f"orbit_{k:05d}.ppm"), img)
    print(f"rendered {len(list(idxs)) if only is None else 1} frame(s) -> {out}")


if __name__ == "__main__":
    main()
