"""Deterministic synthetic media-variant datasets (stdlib-only, no committed assets).

Each *base* is one entity; from it we derive labelled *variants* under named
perturbations (the transforms a real media dedup faces). Two items derived from
the same base are a positive pair; cross-base pairs are negatives. The harness
then hashes every item and measures whether the blocker/scorer keeps the
positives together and the negatives apart — per transform, so we see exactly
which perturbations the hash survives.

No numpy / no goldenmatch import: payloads are plain ``list[list[int]]`` (image
luma) and ``list[float]`` (audio PCM), the decoded-input contract the perceptual
kernel consumes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Grid = list[list[int]]


@dataclass
class Item:
    item_id: int
    base_id: int
    transform: str
    payload: object  # Grid for image; (samples, sample_rate) for audio


@dataclass
class Suite:
    kind: str  # "image" | "audio"
    items: list[Item]
    gt_pairs: set[tuple[int, int]]  # canonical (min,max) positives (same base)
    transform_pairs: dict[str, set[tuple[int, int]]]  # (orig, variant) by transform


def _canon(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


class _LCG:
    """Tiny deterministic PRNG (no `random` import for full reproducibility)."""

    def __init__(self, seed: int) -> None:
        self.s = (seed * 2862933555777941757 + 3037000493) & ((1 << 64) - 1)

    def signed(self, amp: int) -> int:
        self.s = (self.s * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (self.s >> 33) % (2 * amp + 1) - amp


# ----------------------------- image ---------------------------------------- #
_IMG_DCT_BINS = 8  # low-freq DCT signature edge (matches pHash's 8x8 HASH_SIZE)


def _base_image(seed: int, h: int = 48, w: int = 48) -> Grid:
    """A base whose low-frequency DCT *signature* is randomised per seed, built as
    the inverse DCT of that signature.

    pHash reads the sign of each coefficient in the top-left 8x8 DCT block (vs the
    median). The old 3-sinusoid pattern only varied freq by ``seed % {5,7,3}``, so
    many bases shared a low-band sign pattern and *collided* in pHash space -- the
    end-to-end suite saw thousands of cross-base false positives (a dataset
    artifact, not a pipeline limit). Here each base draws a random signed amplitude
    per ``8x8`` DCT bin under a low-pass envelope (``1/(1+ku+kv)`` -- energy
    concentrated in the lowest, photometric-transform-robust frequencies) and
    synthesises the image directly from it. Distinct seeds => distinct sign
    patterns => cross-base Hamming ~32 (validated min 20 over 30 bases, 0 collisions
    at the 0.85 match threshold), while brightness/contrast/blur/noise/recompress
    preserve the dominant low-band signs (per-transform recall stays ~1.0). Rotation
    still breaks pHash by design (the radial feature is the geometric answer)."""
    rng = _LCG(seed * 2654435761 + 1)
    amp = [[0.0] * _IMG_DCT_BINS for _ in range(_IMG_DCT_BINS)]
    for ku in range(_IMG_DCT_BINS):
        for kv in range(_IMG_DCT_BINS):
            if ku == 0 and kv == 0:
                continue  # DC stays mid-gray (128 offset below)
            d = rng.signed(1 << 20)  # ~uniform in [-2^20, 2^20]
            sign = 1.0 if d >= 0 else -1.0
            amp[ku][kv] = sign * (40.0 / (1.0 + ku + kv)) * (0.5 + abs(d) / (1 << 20))
    # Separable inverse DCT-II: pixel = 128 + sum_{ku,kv} A[ku][kv]*cos_x*cos_y.
    cos_x = [[math.cos(math.pi * (x + 0.5) * ku / w) for x in range(w)] for ku in range(_IMG_DCT_BINS)]
    cos_y = [[math.cos(math.pi * (y + 0.5) * kv / h) for y in range(h)] for kv in range(_IMG_DCT_BINS)]
    out: Grid = []
    for y in range(h):
        row = []
        for x in range(w):
            v = 128.0
            for ku in range(_IMG_DCT_BINS):
                cxu = cos_x[ku][x]
                au = amp[ku]
                for kv in range(_IMG_DCT_BINS):
                    a = au[kv]
                    if a != 0.0:
                        v += a * cxu * cos_y[kv][y]
            row.append(max(0, min(255, int(round(v)))))
        out.append(row)
    return out


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _img_brightness(g: Grid) -> Grid:
    return [[_clamp(v * 0.9 + 22) for v in row] for row in g]


def _img_contrast(g: Grid) -> Grid:
    return [[_clamp((v - 128) * 1.25 + 128) for v in row] for row in g]


def _img_blur(g: Grid) -> Grid:
    h, w = len(g), len(g[0])
    out = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            acc = cnt = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w:
                        acc += g[yy][xx]
                        cnt += 1
            out[y][x] = acc // cnt
    return out


def _img_noise(g: Grid, base_id: int) -> Grid:
    rng = _LCG(base_id * 101 + 7)
    return [[_clamp(v + rng.signed(10)) for v in row] for row in g]


def _img_recompress(g: Grid) -> Grid:
    """Downscale by 2 (area average) then nearest-up — a lossy re-encode proxy."""
    h, w = len(g), len(g[0])
    hh, ww = h // 2, w // 2
    small = [
        [(g[2 * y][2 * x] + g[2 * y][2 * x + 1] + g[2 * y + 1][2 * x] + g[2 * y + 1][2 * x + 1]) // 4
         for x in range(ww)]
        for y in range(hh)
    ]
    return [[small[y // 2][x // 2] for x in range(w)] for y in range(h)]


def _img_crop(g: Grid) -> Grid:
    """Center-crop ~88% (the pHash resize re-normalizes the smaller grid)."""
    h, w = len(g), len(g[0])
    my, mx = h // 16, w // 16
    return [row[mx : w - mx] for row in g[my : h - my]]


def _img_rotate(g: Grid, deg: float = 8.0) -> Grid:
    """Small nearest-neighbour rotation about the center — a HARD case (pHash is
    not rotation-invariant); included to measure where the hash breaks down."""
    h, w = len(g), len(g[0])
    cy, cx = (h - 1) / 2, (w - 1) / 2
    rad = math.radians(deg)
    cos, sin = math.cos(rad), math.sin(rad)
    out = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            sx = cx + (x - cx) * cos - (y - cy) * sin
            sy = cy + (x - cx) * sin + (y - cy) * cos
            ix, iy = int(round(sx)), int(round(sy))
            out[y][x] = g[iy][ix] if 0 <= iy < h and 0 <= ix < w else 128
    return out


_IMAGE_TRANSFORMS = ("brightness", "contrast", "blur", "noise", "recompress", "crop", "rotate")


def build_image_suite(n_bases: int = 30) -> Suite:
    items: list[Item] = []
    gt: set[tuple[int, int]] = set()
    tpairs: dict[str, set[tuple[int, int]]] = {t: set() for t in _IMAGE_TRANSFORMS}
    nid = 0
    for b in range(n_bases):
        base = _base_image(b)
        orig_id = nid
        items.append(Item(nid, b, "orig", base))
        nid += 1
        group = [orig_id]
        for t in _IMAGE_TRANSFORMS:
            if t == "brightness":
                payload = _img_brightness(base)
            elif t == "contrast":
                payload = _img_contrast(base)
            elif t == "blur":
                payload = _img_blur(base)
            elif t == "noise":
                payload = _img_noise(base, b)
            elif t == "recompress":
                payload = _img_recompress(base)
            elif t == "crop":
                payload = _img_crop(base)
            else:
                payload = _img_rotate(base)
            items.append(Item(nid, b, t, payload))
            tpairs[t].add(_canon(orig_id, nid))
            group.append(nid)
            nid += 1
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                gt.add(_canon(group[i], group[j]))
    return Suite("image", items, gt, tpairs)


# ----------------------------- audio ---------------------------------------- #
def _base_audio(seed: int, length: int = 16000, sr: int = 44100, k: int = 40) -> list[float]:
    """Broadband audio: ``k`` sinusoids spread across the 300-2000 Hz Haitsma-Kalker
    analysis band -- a realistic-spectrum stand-in for music/speech.

    NOT pure tones (the pre-finding-3 shape): a 3-tone signal leaves most log-bands
    near-empty, so each fingerprint bit is the sign of a ~zero energy difference =
    pure noise, which made the suite report 0.0 noise recall (a dataset artifact,
    not a kernel limit). Broadband energy fills the bands, so the fingerprint is
    actually noise-robust -- which this suite now measures (ADR 0022 finding 3)."""
    rng = _LCG(seed * 2654435761 + 1)
    comps = []
    for _ in range(k):
        f = 300.0 + (rng.signed(1 << 20) + (1 << 20)) / (1 << 21) * 1700.0  # 300..2000 Hz
        ph = (rng.signed(1 << 20) + (1 << 20)) / (1 << 21) * (2 * math.pi)
        comps.append((f, ph))
    out = []
    for n in range(length):
        t = n / sr
        out.append(sum(math.sin(2 * math.pi * f * t + ph) for f, ph in comps) / k)
    return out


def _aud_amplitude(s: list[float]) -> list[float]:
    return [0.5 * v for v in s]


def _aud_noise(s: list[float], base_id: int, snr_db: float = 20.0) -> list[float]:
    """Additive noise at a calibrated signal-to-noise ratio (default 20 dB =
    moderate). Uniform noise scaled so its RMS hits the target SNR; deterministic."""
    rng = _LCG(base_id * 211 + 13)
    s_rms = math.sqrt(sum(v * v for v in s) / len(s)) or 1e-9
    target_n_rms = s_rms / (10.0 ** (snr_db / 20.0))
    scale = target_n_rms * math.sqrt(3.0)  # uniform[-1,1] has RMS 1/sqrt(3)
    return [v + (rng.signed(1 << 20) / (1 << 20)) * scale for v in s]


def _aud_trim(s: list[float]) -> list[float]:
    return s[4096:]  # drop ~2 frames -> time offset, exercises the alignment search


_AUDIO_TRANSFORMS = ("amplitude", "noise", "trim")


def build_audio_suite(n_bases: int = 12, sr: int = 44100) -> Suite:
    items: list[Item] = []
    gt: set[tuple[int, int]] = set()
    tpairs: dict[str, set[tuple[int, int]]] = {t: set() for t in _AUDIO_TRANSFORMS}
    nid = 0
    for b in range(n_bases):
        base = _base_audio(b, sr=sr)
        orig_id = nid
        items.append(Item(nid, b, "orig", (base, sr)))
        nid += 1
        group = [orig_id]
        for t in _AUDIO_TRANSFORMS:
            if t == "amplitude":
                payload = _aud_amplitude(base)
            elif t == "noise":
                payload = _aud_noise(base, b)
            else:
                payload = _aud_trim(base)
            items.append(Item(nid, b, t, (payload, sr)))
            tpairs[t].add(_canon(orig_id, nid))
            group.append(nid)
            nid += 1
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                gt.add(_canon(group[i], group[j]))
    return Suite("audio", items, gt, tpairs)
