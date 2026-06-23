"""Pure-Python reference + fallback for the perceptual-core media-hash kernel.

This module is the **authoritative reference** for the cross-language parity
contract of the multimodal-ER *crawl tier* (ADR 0022). The Rust
``goldenmatch-perceptual-core`` crate reproduces these outputs byte-for-byte;
the committed golden-vector fixture (``tests/fixtures/perceptual_golden.json``)
is generated from this module.

It computes **deterministic, in-house perceptual hashes** — no ML model, no
third-party perceptual-hash library (``imagehash`` / ``pyacoustid`` are out: a
hand-rolled algorithm is the cross-language contract, exactly as ``sketch.py``
hand-rolls its hash family). The hash is a high-signal, fully *auditable* match
feature: similarity is a hamming distance you can show the user.

Keep the math stdlib-only (``math`` + lists), so the reference imports fast and
runs identical floating-point operations to the Rust kernel. ``numpy.fft`` and
friends are intentionally avoided — their tuned transforms do not reproduce a
fixed summation order across languages, and the hash thresholds would flip on the
resulting ULP drift. The transforms here are therefore *direct* (no FFT); the
Rust kernel matches this op-for-op for v1, and a later perf slice can swap in an
FFT gated by the same golden vectors.

Two modalities, both reduced to a bit-string compared by hamming distance:

- **Image** — a 64-bit DCT perceptual hash (pHash). Input is a *decoded* luma
  grid (rows of grayscale values); format decoding (PNG/JPEG) is a thin upstream
  adapter (see ``decode_image_to_luma``), keeping this kernel codec-free.
- **Audio** — a Haitsma-Kalker-style robust hash: a sequence of 32-bit
  sub-fingerprints, one per frame, over log-spaced spectral bands. Input is
  *decoded* mono PCM (see ``decode_audio_to_mono``).

The kernel operates on decoded input by design (ADR 0022) so it stays pyo3-free
and parity-clean, and so bring-your-own-decoded-input is a first-class entrypoint.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from operator import mul

logger = logging.getLogger(__name__)

__all__ = [
    # image
    "IMG_RESIZE",
    "HASH_SIZE",
    "phash_image",
    "phash_image_batch",
    "phash_hex",
    # image: radial-variance (rotation/crop-aware, ADR 0022 finding 1)
    "RADIAL_RESIZE",
    "RADIAL_ANGLES",
    "radial_variance",
    "radial_hex",
    "radial_from_hex",
    "radial_align_similarity",
    # audio
    "AUDIO_FRAME",
    "AUDIO_HOP",
    "AUDIO_BANDS",
    "AUDIO_F_MIN",
    "AUDIO_F_MAX",
    "fingerprint_audio",
    # comparison
    "popcount",
    "hamming",
    "audio_ber",
    "audio_ber_aligned",
    "audio_fp_hex",
    "audio_fp_from_hex",
    # optional decode adapters (lazy heavy imports)
    "decode_image_to_luma",
    "decode_audio_to_mono",
]

# --- image pHash parameters (the byte-exact contract) ------------------------
IMG_RESIZE = 32  # downscale square edge before the DCT
HASH_SIZE = 8  # take the top-left HASH_SIZE x HASH_SIZE low-frequency block -> 64 bits

# --- audio fingerprint parameters --------------------------------------------
AUDIO_FRAME = 4096  # samples per analysis frame
AUDIO_HOP = 2048  # frame advance (50% overlap)
AUDIO_BANDS = 33  # log-spaced energy bands -> 32 bits per sub-fingerprint
AUDIO_F_MIN = 300.0  # Hz, low edge of the analysed band (Haitsma-Kalker range)
AUDIO_F_MAX = 2000.0  # Hz, high edge

_TWO_PI = 2.0 * math.pi


# ============================== bit helpers ==================================
def popcount(x: int) -> int:
    """Number of set bits in a non-negative int."""
    return int(x).bit_count()


def hamming(a: int, b: int) -> int:
    """Hamming distance between two equal-width bit-packed hashes."""
    return (a ^ b).bit_count()


# ============================== image: pHash =================================
def _as_grid(luma) -> list[list[float]]:
    """Coerce a 2D luma input (nested sequence or array-like) to list[list[float]].

    Accepts any rectangular grid of numbers (0..255 conventional, but only the
    relative ordering matters to the hash). Rejects ragged / empty input.
    """
    rows = [list(r) for r in luma]
    if not rows or not rows[0]:
        raise ValueError("luma grid must be non-empty")
    width = len(rows[0])
    grid: list[list[float]] = []
    for r in rows:
        if len(r) != width:
            raise ValueError("luma grid rows must all have the same length")
        grid.append([float(v) for v in r])
    return grid


def _bilinear_resize(grid: list[list[float]], size: int) -> list[list[float]]:
    """Resize ``grid`` to ``size`` x ``size`` via align-corners bilinear sampling.

    Output coordinate ``o`` maps to source ``o * (n - 1) / (size - 1)`` (so the
    corners are preserved). A degenerate source dimension of length 1 maps every
    output coordinate to index 0.
    """
    h = len(grid)
    w = len(grid[0])
    out = [[0.0] * size for _ in range(size)]
    denom = size - 1 if size > 1 else 1

    def _src_coords(n: int):
        coords = []
        for o in range(size):
            if n == 1:
                coords.append((0, 0, 0.0))
                continue
            s = o * (n - 1) / denom
            i0 = int(math.floor(s))
            if i0 >= n - 1:
                i0 = n - 2
            i1 = i0 + 1
            coords.append((i0, i1, s - i0))
        return coords

    ys = _src_coords(h)
    xs = _src_coords(w)
    for oy in range(size):
        y0, y1, wy = ys[oy]
        row0 = grid[y0]
        row1 = grid[y1]
        orow = out[oy]
        for ox in range(size):
            x0, x1, wx = xs[ox]
            top = row0[x0] * (1.0 - wx) + row0[x1] * wx
            bot = row1[x0] * (1.0 - wx) + row1[x1] * wx
            orow[ox] = top * (1.0 - wy) + bot * wy
    return out


def _dct1d_matrix(n: int) -> list[list[float]]:
    """Precompute the unnormalized DCT-II basis: ``M[k][i] = cos(pi*(i+0.5)*k/n)``."""
    return [
        [math.cos(math.pi * (i + 0.5) * k / n) for i in range(n)]
        for k in range(n)
    ]


_DCT_M = _dct1d_matrix(IMG_RESIZE)


def _dct2_topleft(block: list[list[float]], size: int, keep: int) -> list[list[float]]:
    """2D separable DCT-II of ``block`` (size x size); return the top-left keep x keep.

    Rows are transformed first, then columns, in that fixed order. Only the
    ``keep`` lowest-frequency output rows/columns are materialised.
    """
    m = _DCT_M
    # DCT along rows -> tmp[i][k] for k in 0..keep-1
    tmp = [[0.0] * keep for _ in range(size)]
    for i in range(size):
        brow = block[i]
        trow = tmp[i]
        for k in range(keep):
            mk = m[k]
            acc = 0.0
            for x in range(size):
                acc += brow[x] * mk[x]
            trow[k] = acc
    # DCT along columns -> out[k][l]
    out = [[0.0] * keep for _ in range(keep)]
    for l in range(keep):
        for k in range(keep):
            mk = m[k]
            acc = 0.0
            for y in range(size):
                acc += tmp[y][l] * mk[y]
            out[k][l] = acc
    return out


def _phash_image_python(grid: list[list[float]]) -> int:
    """The pure-Python pHash over an already-coerced luma grid (parity reference)."""
    small = _bilinear_resize(grid, IMG_RESIZE)
    block = _dct2_topleft(small, IMG_RESIZE, HASH_SIZE)
    coeffs = [block[r][c] for r in range(HASH_SIZE) for c in range(HASH_SIZE)]
    ordered = sorted(coeffs)
    n = len(ordered)
    # even count: median is the mean of the two central order statistics
    median = (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    h = 0
    for i, v in enumerate(coeffs):
        if v > median:
            h |= 1 << i
    return h


def phash_image(luma) -> int:
    """64-bit DCT perceptual hash of a decoded luma grid.

    Pipeline: coerce -> align-corners bilinear resize to 32x32 -> 2D DCT-II ->
    take the 8x8 low-frequency block -> threshold each coefficient against the
    median of the 64 coefficients. Bit ``i = row*8 + col`` is set (LSB-first) when
    the coefficient strictly exceeds the median; exact ties resolve to 0.

    Returns an int in ``[0, 2**64)``. Compare two hashes with :func:`hamming`.
    Uses the native kernel when gated on (``native_enabled("perceptual")``).
    """
    grid = _as_grid(luma)
    # Lazy import: keep this module's top level dependency-light (stdlib only).
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("perceptual"):
        try:
            return native_module().perceptual_phash_image(grid)
        except AttributeError:
            # Published wheel predates this symbol (wheel/caller skew, see #688) —
            # legitimate fallback. A real kernel error is NOT swallowed here.
            logger.debug(
                "native perceptual_phash_image unavailable (wheel skew); using Python fallback"
            )
    return _phash_image_python(grid)


def phash_hex(h: int) -> str:
    """Canonical fixed-width (16 hex char / 64-bit) string form of an image pHash.

    The format the ``perceptual`` blocking strategy and the ``phash`` scorer
    consume in a match column (``f"{h:016x}"``).
    """
    return format(h, "016x")


def phash_image_batch(images: Sequence) -> list[int]:
    """Per-image 64-bit pHash for many decoded luma grids (the column path).

    Uses the native batch kernel when gated on; otherwise hashes each grid with
    the pure-Python path. Output is identical to mapping :func:`phash_image`.
    """
    grids = [_as_grid(im) for im in images]
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("perceptual"):
        try:
            return native_module().perceptual_phash_batch(grids)
        except AttributeError:
            logger.debug(
                "native perceptual_phash_batch unavailable (wheel skew); using Python fallback"
            )
    return [_phash_image_python(g) for g in grids]


# ===================== image: radial-variance (geometric) ====================
# pHash is photometric, not geometric: the bench harness measured 0.0 recall on
# rotation and crop (ADR 0022, finding 1). The radial-variance profile is the
# proven rotation-AWARE answer (pHash's own `ph_image_digest` radial hash): a
# feature vector of per-angle pixel variance, compared by sliding to the best
# angular alignment (cyclic, the way :func:`audio_ber_aligned` slides over time
# offset). Orientation is preserved in the vector (so it discriminates, unlike a
# rotation-INVARIANT descriptor which collapses different images together), while
# the alignment search absorbs rotation. Validated: crop 0.0->1.0, rotate
# 0.0->0.95, photometric transforms stay 1.0, match/non-match separation 0.46.
RADIAL_RESIZE = 32  # square edge the variance profile is sampled on
RADIAL_ANGLES = 48  # angular bins over 0..pi (a line is undirected -> half turn)


def _radial_variance_python(grid: list[list[float]]) -> list[float]:
    """Per-angle pixel-variance profile over an align-corners resize (parity ref).

    For each of ``RADIAL_ANGLES`` lines through the image center, sample the luma
    at half-pixel steps (nearest-neighbour, banker's rounding to match the Rust
    kernel) and take the variance of the sampled values. Rotation cyclically
    shifts this profile; :func:`radial_align_similarity` searches that shift.
    """
    small = _bilinear_resize(grid, RADIAL_RESIZE)
    n = RADIAL_RESIZE
    center = (n - 1) / 2.0
    steps = [i * 0.5 for i in range(-2 * n, 2 * n + 1)]
    profile: list[float] = []
    for line in range(RADIAL_ANGLES):
        theta = math.pi * line / RADIAL_ANGLES
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        vals: list[float] = []
        for t in steps:
            x = int(round(center + t * cos_t))
            y = int(round(center + t * sin_t))
            if 0 <= x < n and 0 <= y < n:
                vals.append(small[y][x])
        if len(vals) < 2:
            profile.append(0.0)
            continue
        mean = sum(vals) / len(vals)
        profile.append(sum((v - mean) * (v - mean) for v in vals) / len(vals))
    return profile


def radial_variance(luma) -> list[float]:
    """Rotation-aware radial-variance profile of a decoded luma grid (ADR 0022).

    A ``RADIAL_ANGLES``-length float vector; the geometric counterpart to the
    photometric :func:`phash_image`. Compare two profiles with
    :func:`radial_align_similarity` (rotation-aligned) or store the compact column
    form via :func:`radial_hex`. Uses the native kernel when gated on.
    """
    grid = _as_grid(luma)
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("perceptual"):
        try:
            return native_module().perceptual_radial_variance(grid)
        except AttributeError:
            logger.debug(
                "native perceptual_radial_variance unavailable (wheel skew); using Python fallback"
            )
    return _radial_variance_python(grid)


def radial_hex(profile: Sequence[float]) -> str:
    """Canonical column form of a radial-variance profile: z-normalise then quantise
    each bin to a signed byte (2 hex chars).

    The comparison (:func:`radial_align_similarity`) is affine-invariant (Pearson
    over the best angular shift), so the z-normalise + int8 quantise is lossless
    for scoring while giving a fixed-width hex string a match column can hold. A
    constant profile (zero variance everywhere) encodes as all-zero bytes.
    """
    p = [float(v) for v in profile]
    n = len(p)
    if n == 0:
        return ""
    mean = sum(p) / n
    std = math.sqrt(sum((v - mean) * (v - mean) for v in p) / n)
    if std == 0.0:
        return "00" * n
    out = []
    for v in p:
        q = int(round((v - mean) / std * 32.0))
        q = max(-127, min(127, q))
        out.append(format(q & 0xFF, "02x"))
    return "".join(out)


def radial_from_hex(s: str) -> list[int]:
    """Inverse of :func:`radial_hex` — parse a 2-hex-char-per-bin signed-byte
    profile (``0x`` prefix tolerated) back to a list of ints."""
    if s[:2] in ("0x", "0X"):
        s = s[2:]
    usable = len(s) - (len(s) % 2)
    out: list[int] = []
    for i in range(0, usable, 2):
        b = int(s[i : i + 2], 16)
        out.append(b - 256 if b >= 128 else b)
    return out


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    """Pearson correlation of two equal-length sequences; 0.0 if either is constant."""
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    da = sum((x - ma) * (x - ma) for x in a)
    db = sum((y - mb) * (y - mb) for y in b)
    if da == 0.0 or db == 0.0:
        return 0.0
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / math.sqrt(da * db)


def radial_align_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Rotation-aligned similarity of two radial-variance profiles in ``[0, 1]``.

    Maximum Pearson correlation over all cyclic angular shifts of ``b`` against
    ``a`` — rotation just rotates the profile, so the best shift recovers it (the
    angular counterpart to :func:`audio_ber_aligned`'s time-offset search).
    Negative correlations (unrelated images) clamp to 0.0; mismatched lengths or
    empty input -> 0.0. This is the scoring-side comparison and stays pure-Python
    (the kernel parity contract is the profile, not the compare)."""
    la = len(a)
    if la == 0 or len(b) != la:
        return 0.0
    best = -1.0
    for shift in range(la):
        rotated = list(b[shift:]) + list(b[:shift])
        c = _pearson(a, rotated)
        if c > best:
            best = c
    return max(0.0, min(1.0, best))


# ============================== audio fingerprint ============================
def _hann(n: int) -> list[float]:
    if n == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(_TWO_PI * i / (n - 1)) for i in range(n)]


_HANN = _hann(AUDIO_FRAME)


def _band_bins(sample_rate: int) -> list[int]:
    """DFT bin index of each of the ``AUDIO_BANDS + 1`` log-spaced band edges."""
    ratio = AUDIO_F_MAX / AUDIO_F_MIN
    edges = []
    for i in range(AUDIO_BANDS + 1):
        freq = AUDIO_F_MIN * (ratio ** (i / AUDIO_BANDS))
        edges.append(int(round(freq * AUDIO_FRAME / sample_rate)))
    return edges


# Per-sample-rate cache of (band_bins, lo, cos_table, sin_table). The DFT only
# evaluates bins lo..hi-1, so the twiddle tables are computed once and reused
# across frames. Caching changes neither the values nor the summation order, so
# parity with the Rust kernel (which precomputes the same twiddles) holds.
_TWIDDLE: dict[int, tuple] = {}


def _twiddles(sample_rate: int):
    cached = _TWIDDLE.get(sample_rate)
    if cached is not None:
        return cached
    band_bins = _band_bins(sample_rate)
    lo = band_bins[0]
    hi = band_bins[-1]
    n = AUDIO_FRAME
    cos_table: list[list[float]] = []
    sin_table: list[list[float]] = []
    for k in range(lo, hi):
        ang = -_TWO_PI * k / n
        cos_table.append([math.cos(ang * idx) for idx in range(n)])
        sin_table.append([math.sin(ang * idx) for idx in range(n)])
    cached = (band_bins, lo, cos_table, sin_table)
    _TWIDDLE[sample_rate] = cached
    return cached


def _frame_band_energies(samples: Sequence[float], start: int, tw) -> list[float]:
    """Hann-windowed band energies for one frame via a direct (partial) DFT.

    Only the bins spanned by the band edges are evaluated. Energy of band ``m`` is
    the summed magnitude-squared over bins ``[band_bins[m], band_bins[m+1])``.
    """
    band_bins, lo, cos_table, sin_table = tw
    n = AUDIO_FRAME
    frame = [_HANN[i] * samples[start + i] for i in range(n)]
    mags: list[float] = []  # |X[k]|^2 for k in [lo, hi)
    # `sum(map(mul, ...))` accumulates left-to-right starting from 0 exactly as
    # the naive `re += x * row_c[idx]` loop did, so it is bit-identical to the
    # Rust kernel and golden vectors -- the multiply/accumulate just moves into C
    # (~1.7x over the Python loop). Do NOT swap in math.fsum/numpy: that changes
    # the summation order and breaks cross-language parity.
    for row_c, row_s in zip(cos_table, sin_table):
        re = sum(map(mul, frame, row_c))
        im = sum(map(mul, frame, row_s))
        mags.append(re * re + im * im)
    energies = []
    for m in range(AUDIO_BANDS):
        energies.append(sum(mags[k - lo] for k in range(band_bins[m], band_bins[m + 1])))
    return energies


def fingerprint_audio(samples: Sequence[float], sample_rate: int) -> list[int]:
    """Haitsma-Kalker-style robust audio fingerprint of decoded mono PCM.

    Frames the signal (``AUDIO_FRAME`` / ``AUDIO_HOP``), computes ``AUDIO_BANDS``
    log-spaced band energies per frame, and emits one 32-bit sub-fingerprint per
    frame transition::

        bit(n, m) = 1 if (E[n,m] - E[n,m+1]) - (E[n-1,m] - E[n-1,m+1]) > 0 else 0

    for ``m`` in ``0..31`` (LSB-first). Returns a list of ints; a list with one
    fewer entry than the number of frames. The signal is zero-padded to at least
    two frames so at least one sub-fingerprint is always produced.

    Compare two fingerprints with :func:`audio_ber`.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    data = [float(v) for v in samples]
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("perceptual"):
        try:
            return native_module().perceptual_fingerprint_audio(data, sample_rate)
        except AttributeError:
            logger.debug(
                "native perceptual_fingerprint_audio unavailable (wheel skew); using Python fallback"
            )
    return _fingerprint_audio_python(data, sample_rate)


def _fingerprint_audio_python(data: list[float], sample_rate: int) -> list[int]:
    """The pure-Python audio fingerprint over already-float samples (parity reference)."""
    min_len = AUDIO_FRAME + AUDIO_HOP  # guarantees >= 2 frames
    if len(data) < min_len:
        data = data + [0.0] * (min_len - len(data))
    n_frames = 1 + (len(data) - AUDIO_FRAME) // AUDIO_HOP
    tw = _twiddles(sample_rate)

    prev = _frame_band_energies(data, 0, tw)
    out: list[int] = []
    for f in range(1, n_frames):
        cur = _frame_band_energies(data, f * AUDIO_HOP, tw)
        word = 0
        for m in range(AUDIO_BANDS - 1):
            d = (cur[m] - cur[m + 1]) - (prev[m] - prev[m + 1])
            if d > 0.0:
                word |= 1 << m
        out.append(word)
        prev = cur
    return out


def audio_ber(fp_a: Sequence[int], fp_b: Sequence[int]) -> float:
    """Bit-error-rate between two audio fingerprints, frame-aligned over the
    shorter length. 0.0 == identical, ~0.5 == unrelated. Empty inputs -> 1.0.

    Sub-fingerprint alignment/offset search is a blocker concern (ADR 0022) and is
    out of scope here; this is the simple frame-aligned baseline.
    """
    n = min(len(fp_a), len(fp_b))
    if n == 0:
        return 1.0
    bits = 0
    for i in range(n):
        bits += (fp_a[i] ^ fp_b[i]).bit_count()
    return bits / (n * (AUDIO_BANDS - 1))


def audio_ber_aligned(
    fp_a: Sequence[int], fp_b: Sequence[int], min_overlap: int = 8
) -> float:
    """Best (minimum) bit-error-rate over all frame offsets of one fingerprint
    against the other — robust to recordings that start at different points.

    Slides ``fp_b`` across ``fp_a`` and returns the smallest BER over any overlap
    of at least ``min_overlap`` frames (capped at the shorter length, so short
    inputs still align). 0.0 == a perfectly-aligned identical stretch; ~0.5 ==
    unrelated. Empty inputs -> 1.0. This is the offset-search ADR 0022 flagged as
    the scoring-side counterpart to the frame-aligned :func:`audio_ber`.
    """
    a = list(fp_a)
    b = list(fp_b)
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 1.0
    need = min(min_overlap, la, lb)
    nb = AUDIO_BANDS - 1
    best = 1.0
    # offset = index in `a` that `b[0]` aligns to (negative = b starts earlier)
    for off in range(-(lb - 1), la):
        lo = max(0, off)
        hi = min(la, off + lb)
        overlap = hi - lo
        if overlap < need:
            continue
        bits = 0
        for i in range(lo, hi):
            bits += (a[i] ^ b[i - off]).bit_count()
        ber = bits / (overlap * nb)
        if ber < best:
            best = ber
    return best


def audio_fp_hex(fp: Sequence[int]) -> str:
    """Canonical column form of an audio fingerprint: each 32-bit sub-fingerprint
    as 8 fixed-width hex chars, concatenated (the form the ``audio_fp`` scorer
    consumes)."""
    return "".join(format(int(w) & 0xFFFFFFFF, "08x") for w in fp)


def audio_fp_from_hex(s: str) -> list[int]:
    """Inverse of :func:`audio_fp_hex` — parse a concatenated 8-hex-char-per-word
    fingerprint string (``0x`` prefix tolerated) back to a list of ints."""
    if s[:2] in ("0x", "0X"):
        s = s[2:]
    usable = len(s) - (len(s) % 8)
    return [int(s[i : i + 8], 16) for i in range(0, usable, 8)]


# ===================== optional decode adapters (upstream) ===================
def decode_image_to_luma(path: str) -> list[list[float]]:
    """Decode an image file to a luma grid using Pillow (optional dependency).

    Kept out of the byte-exact kernel on purpose (ADR 0022): decoding is an
    upstream adapter, so the kernel stays codec-free and parity-clean. Requires
    ``goldenmatch[vision]`` (Pillow).
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional path
        raise ImportError(
            "decode_image_to_luma requires Pillow. Install with: "
            "pip install goldenmatch[vision]"
        ) from exc
    with Image.open(path) as img:
        grey = img.convert("L")
        w, h = grey.size
        px = list(grey.getdata())
    return [[float(px[y * w + x]) for x in range(w)] for y in range(h)]


def decode_audio_to_mono(path: str) -> tuple[list[float], int]:
    """Decode an audio file to (mono samples, sample_rate) using soundfile.

    Optional upstream adapter (ADR 0022); requires ``goldenmatch[audio]``
    (soundfile). Multi-channel audio is averaged to mono.
    """
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - optional path
        raise ImportError(
            "decode_audio_to_mono requires soundfile. Install with: "
            "pip install goldenmatch[audio]"
        ) from exc
    data, sr = sf.read(path, dtype="float64", always_2d=True)
    mono = [float(sum(frame) / len(frame)) for frame in data]
    return mono, int(sr)
