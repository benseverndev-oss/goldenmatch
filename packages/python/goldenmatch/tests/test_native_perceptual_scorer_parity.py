"""Parity for the radial / audio_fp bucket kernels (score-core ids 13/14) vs the
pure Python references `_radial_score_single` / `_audio_fp_score_single`.

Two different parity bars, by numeric shape:
  * `audio_fp` is BYTE-EXACT (`==`): its BER is integer popcount + one f64 divide
    (no floating reduction), so the native kernel and the pure mirror agree to the
    bit, the same bar the integer-popcount bloom scorers hold.
  * `radial` uses a tight FLOAT TOLERANCE: its Pearson sums three f64 reductions
    (mean, the two variances, the covariance), and LLVM auto-vectorizes those
    reduction loops in the release build with a SIMD summation order that differs
    from Python's strict left-to-right `sum()` by ~1 ULP -- and the exact ULP is
    CPU/codegen-dependent (it agreed on the dev box, diverged on a CI runner). This
    is the same reduction-order reality the ensemble kernel handles with a tolerance;
    a real reimpl bug (wrong formula / mis-clamp / wrong parse) shifts the score by
    >> the tolerance. The score-core cargo test still pins radial's EXACT anchors
    (identity/rotation -> 1.0, constant/mismatch -> 0.0, which are reduction-order
    invariant).

On UNPARSEABLE hex the pure mirrors RAISE (Python `int(..., 16)`) while the kernel
declines to 0.0 (score_one cannot raise); that native-only contract is asserted in
the score-core cargo test, so this corpus stays on valid hex where both agree.

Reaching 17/19 kernel-backed scorers: docs-site/suite-matrix.mdx.
"""
from __future__ import annotations

import random

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core import scorer as _scorer

# radial Pearson: f64 reduction order diverges ~1 ULP across CPUs (see module
# docstring). 1e-9 is ~7 orders above the observed drift, far below any real bug.
_RADIAL_TOL = 1e-9


def _rand_hex(rng: random.Random, n_chars: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n_chars))


def _radial_corpus() -> list[tuple[str, str]]:
    """2-hex-chars-per-bin signed-byte profiles."""
    rng = random.Random(20260721)
    pairs: list[tuple[str, str]] = []
    for _ in range(1500):
        bins = rng.choice([4, 8, 16, 24, 32])
        a = _rand_hex(rng, bins * 2)
        # same length (meaningful non-zero align) most of the time; differing some
        b_bins = bins if rng.random() < 0.75 else rng.choice([4, 8, 16, 24, 32])
        b = _rand_hex(rng, b_bins * 2)
        pairs.append((a, b))
    # Edges: identical, cyclic rotation, constant profile, 0x prefix, odd length,
    # empty, mismatched length, unparseable.
    same = "01ff02aa10"
    pairs += [
        (same, same),                    # identity -> 1.0
        (same, "ff02aa1001"),            # a cyclic rotation -> 1.0
        ("101010", "202020"),            # constant a -> variance 0 -> 0.0
        ("0x01ff02", "01ff02"),          # 0x prefix stripped both sides
        ("01ff0", "01ff02"),             # odd length: trailing nibble dropped
        ("", ""),                        # empty -> 0.0
        ("0102", "010203"),              # length mismatch -> 0.0
        ("8000", "007f"),                # signed-byte boundary (0x80=-128, 0x7f=127)
    ]
    return pairs


def _audio_corpus() -> list[tuple[str, str]]:
    """8-hex-chars-per-word 32-bit sub-fingerprints, offset alignment search."""
    rng = random.Random(20260722)
    pairs: list[tuple[str, str]] = []
    for _ in range(1500):
        wa = rng.choice([1, 2, 4, 8, 12])
        wb = wa if rng.random() < 0.6 else rng.choice([1, 2, 4, 8, 12])
        pairs.append((_rand_hex(rng, wa * 8), _rand_hex(rng, wb * 8)))
    frag = "deadbeefcafef00d0badc0de"
    pairs += [
        (frag, frag),                          # aligned identical -> 1.0
        ("ffffffff", "00000000"),              # all 32 bits differ -> 0.0
        ("0x0000000100000002", "0000000100000002"),  # 0x prefix
        ("0000000000000001", "00000001"),      # offset search recovers the word
        ("", ""),                              # empty -> 1 - 1.0 = 0.0
        ("00000001", ""),                      # one empty -> 0.0
        ("0000001", "00000001"),               # sub-word remainder dropped
    ]
    return pairs


def _kernel_or_skip(symbol: str):
    n = _native_loader.native_module()
    if n is None or not hasattr(n, symbol):
        pytest.skip(f"native kernel not built / wheel predates {symbol}")
    return n


def test_radial_native_matches_pure_mirror():
    n = _kernel_or_skip("radial_similarity")
    for a, b in _radial_corpus():
        got = n.radial_similarity(a, b)
        want = _scorer._radial_score_single(a, b)
        assert got == pytest.approx(want, abs=_RADIAL_TOL), (
            f"radial {a!r} {b!r}: {got!r} vs {want!r}"
        )


def test_audio_fp_native_matches_pure_mirror():
    n = _kernel_or_skip("audio_fp_similarity")
    for a, b in _audio_corpus():
        got = n.audio_fp_similarity(a, b)
        want = _scorer._audio_fp_score_single(a, b)
        assert got == want, f"audio_fp {a!r} {b!r}: {got!r} vs {want!r}"  # integer -> exact


def test_radial_bucket_kernel_id_13_matches_mirror():
    """score_block_pairs dispatching id 13 == the per-pair mirror."""
    n = _kernel_or_skip("radial_similarity")
    values = ["01ff02aa10", "ff02aa1001", "101010aabb", "00112233ff", "8000ff7f01"]
    row_ids = list(range(len(values)))
    emitted = n.score_block_pairs(
        row_ids, [len(values)], [values], [13], [1.0], 1.0, 0.0, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            want = _scorer._radial_score_single(values[i], values[j])
            if want >= 0.0:  # threshold 0.0 emits every real pair
                assert got[(i, j)] == pytest.approx(want, abs=_RADIAL_TOL), (
                    f"id=13 {values[i]!r} {values[j]!r}"
                )


def test_audio_fp_bucket_kernel_id_14_matches_mirror():
    """score_block_pairs dispatching id 14 == the per-pair mirror."""
    n = _kernel_or_skip("audio_fp_similarity")
    values = ["deadbeefcafef00d", "cafef00ddeadbeef", "00000001", "0000000100000002",
              "ffffffff00000000"]
    row_ids = list(range(len(values)))
    emitted = n.score_block_pairs(
        row_ids, [len(values)], [values], [14], [1.0], 1.0, 0.0, []
    )
    got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            want = _scorer._audio_fp_score_single(values[i], values[j])
            assert got[(i, j)] == want, f"id=14 {values[i]!r} {values[j]!r}"
