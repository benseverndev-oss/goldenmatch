"""Pure-Python reference + fallback for the sketch-core MinHash/LSH kernel.

This module is the **authoritative reference** for the cross-language parity
contract (see ``docs/superpowers/specs/2026-06-19-minhash-lsh-sketch-core-design.md``).
The Rust ``goldenmatch-sketch-core`` crate and the TypeScript port reproduce
these outputs byte-for-byte. The committed golden-vector fixture
(``tests/fixtures/sketch_golden.json``) is generated from this module.

Keep this module dependency-light: stdlib only, plus a lazy import of the native
loader inside the batch entry points. Importing it must not pull polars or any
heavy dependency, so its unit tests run fast and in isolation.

Algorithm (all ``u64`` arithmetic is wrapping unless a modulus is given):

- ``base_hash`` — FNV-1a over UTF-8 bytes, then a splitmix64 finalizer.
- ``splitmix64`` — increment-before-finalize; a stream seeded at ``S`` yields its
  first value as ``finalize(S + GAMMA)`` (there is no raw-seed draw).
- ``shingle`` — char (code-point) or word (split on the exact 6-code-point ASCII
  whitespace set) k-grams, hashed and returned sorted+deduped.
- ``signature`` — N MinHash permutations ``(a_i * x + b_i) mod (2**61 - 1)``,
  coefficients from a splitmix64 stream.
- ``band_hashes`` — banded LSH bucket ids over little-endian signature bytes.
- ``optimal_bands`` — host-side (b, r) selection; not part of the byte-exact path.
"""
from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "base_hash",
    "splitmix64",
    "shingle",
    "signature",
    "estimate_jaccard",
    "band_hashes",
    "optimal_bands",
    "sketch_band_hashes",
    "band_hashes_batch",
    "signature_batch",
]

_MASK64 = (1 << 64) - 1
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x00000100000001B3
_SM_C1 = 0xBF58476D1CE4E5B9
_SM_C2 = 0x94D049BB133111EB
_SM_GAMMA = 0x9E3779B97F4A7C15
_MERSENNE_P = (1 << 61) - 1

# Exactly these six code points are word-mode separators. NOT a language default
# whitespace splitter (those disagree on Unicode whitespace and break parity).
_ASCII_WS = frozenset({0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20})


def base_hash(data: bytes) -> int:
    """FNV-1a (64-bit) over ``data`` then a splitmix64 finalizer."""
    h = _FNV_OFFSET
    for byte in data:
        h = ((h ^ byte) * _FNV_PRIME) & _MASK64
    h = ((h ^ (h >> 30)) * _SM_C1) & _MASK64
    h = ((h ^ (h >> 27)) * _SM_C2) & _MASK64
    return (h ^ (h >> 31)) & _MASK64


def splitmix64(state: int) -> tuple[int, int]:
    """One splitmix64 step. Returns ``(value, new_state)``.

    The increment is applied *before* finalization, so a stream seeded at ``S``
    produces its first value as ``finalize(S + GAMMA)``.
    """
    state = (state + _SM_GAMMA) & _MASK64
    z = state
    z = ((z ^ (z >> 30)) * _SM_C1) & _MASK64
    z = ((z ^ (z >> 27)) * _SM_C2) & _MASK64
    z = (z ^ (z >> 31)) & _MASK64
    return z, state


def _word_tokens(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ord(ch) in _ASCII_WS:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def shingle(text: str, mode: str = "char", k: int = 3) -> list[int]:
    """Return the sorted, deduplicated set of shingle hashes for ``text``.

    ``mode="char"`` windows over Unicode code points; ``mode="word"`` windows
    over tokens split on the ASCII whitespace set. ``n == 0`` (empty or, in word
    mode, whitespace-only) yields the empty set; ``1 <= n < k`` yields a single
    whole-sequence shingle.

    ``k`` must be >= 1. This is enforced (rather than relied on) so all three
    language ports reject ``k < 1`` identically — Rust ``windows(0)`` panics
    where Python would silently produce empty-string shingles, which would be a
    parity divergence.
    """
    if k < 1:
        raise ValueError(f"shingle k must be >= 1, got {k}")
    if mode == "char":
        units: list[str] = list(text)
        sep = ""
    elif mode == "word":
        units = _word_tokens(text)
        sep = " "
    else:
        raise ValueError(f"unknown shingle mode: {mode!r}")
    n = len(units)
    if n == 0:
        return []
    hs: set[int] = set()
    if n < k:
        hs.add(base_hash(sep.join(units).encode("utf-8")))
    else:
        for i in range(n - k + 1):
            hs.add(base_hash(sep.join(units[i : i + k]).encode("utf-8")))
    return sorted(hs)


def _coefficients(num_perms: int, seed: int) -> tuple[list[int], list[int]]:
    a: list[int] = []
    b: list[int] = []
    state = seed
    for _ in range(num_perms):
        v, state = splitmix64(state)
        a.append((v % (_MERSENNE_P - 1)) + 1)
        v, state = splitmix64(state)
        b.append(v % _MERSENNE_P)
    return a, b


def signature(shingles: list[int], num_perms: int, seed: int) -> list[int]:
    """MinHash signature of a shingle set. Empty set => all ``u64::MAX``."""
    a, b = _coefficients(num_perms, seed)
    sig = [_MASK64] * num_perms
    for i in range(num_perms):
        ai, bi, m = a[i], b[i], _MASK64
        for x in shingles:
            p = (ai * (x % _MERSENNE_P) + bi) % _MERSENNE_P
            if p < m:
                m = p
        sig[i] = m
    return sig


def estimate_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    """Estimated Jaccard similarity = fraction of equal signature positions."""
    if not sig_a:
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def band_hashes(sig: list[int], num_bands: int) -> list[int]:
    """Banded-LSH bucket id per band. ``len(sig)`` must be divisible by num_bands."""
    n = len(sig)
    if num_bands <= 0 or n % num_bands != 0:
        raise ValueError(f"num_perms {n} not divisible by num_bands {num_bands}")
    r = n // num_bands
    out: list[int] = []
    for band in range(num_bands):
        buf = band.to_bytes(8, "little")
        for j in range(r):
            buf += sig[band * r + j].to_bytes(8, "little")
        out.append(base_hash(buf))
    return out


def optimal_bands(num_perms: int, threshold: float, steps: int = 1000) -> tuple[int, int]:
    """Pick (num_bands, rows_per_band) whose LSH S-curve best matches ``threshold``.

    Host-side helper only — its result feeds ``band_hashes`` as an explicit
    ``num_bands``; it is never on the byte-exact hash path. Deterministic: a fixed
    1000-step trapezoidal integral and an ascending scan that keeps the smaller
    ``b`` on ties.
    """

    def integral(lo: float, hi: float, f: Callable[[float], float]) -> float:
        h = (hi - lo) / steps
        s = 0.5 * (f(lo) + f(hi))
        for i in range(1, steps):
            s += f(lo + i * h)
        return s * h

    best: tuple[int, int, float] | None = None
    for b in range(1, num_perms + 1):
        if num_perms % b:
            continue
        r = num_perms // b
        pc = lambda s, _r=r, _b=b: 1.0 - (1.0 - s**_r) ** _b
        err = 0.5 * integral(0.0, threshold, pc) + 0.5 * integral(
            threshold, 1.0, lambda s: 1.0 - pc(s)
        )
        if best is None or err < best[2] - 1e-12:
            best = (b, r, err)
    assert best is not None
    return best[0], best[1]


def sketch_band_hashes(
    text: str,
    mode: str = "char",
    k: int = 3,
    num_perms: int = 128,
    num_bands: int = 32,
    seed: int = 0,
) -> list[int]:
    """End-to-end: ``text`` -> shingle -> signature -> band hashes (pure Python)."""
    return band_hashes(signature(shingle(text, mode, k), num_perms, seed), num_bands)


def _band_hashes_batch_python(
    texts: list[str], mode: str, k: int, num_perms: int, num_bands: int, seed: int
) -> list[list[int]]:
    return [sketch_band_hashes(t, mode, k, num_perms, num_bands, seed) for t in texts]


def _signature_batch_python(
    texts: list[str], mode: str, k: int, num_perms: int, seed: int
) -> list[list[int]]:
    return [signature(shingle(t, mode, k), num_perms, seed) for t in texts]


def band_hashes_batch(
    texts: list[str],
    mode: str = "char",
    k: int = 3,
    num_perms: int = 128,
    num_bands: int = 32,
    seed: int = 0,
) -> list[list[int]]:
    """Per-record band hashes for many texts. Uses the native kernel when gated on."""
    try:
        from goldenmatch.core._native_loader import native_enabled, native_module

        if native_enabled("sketch"):
            return native_module().sketch_band_hashes_batch(
                list(texts), mode, k, num_perms, num_bands, seed
            )
    except Exception:
        # Any loader/native problem falls back to the pure-Python reference.
        pass
    return _band_hashes_batch_python(list(texts), mode, k, num_perms, num_bands, seed)


def signature_batch(
    texts: list[str],
    mode: str = "char",
    k: int = 3,
    num_perms: int = 128,
    seed: int = 0,
) -> list[list[int]]:
    """Per-record MinHash signatures for many texts. Uses the native kernel when gated on."""
    try:
        from goldenmatch.core._native_loader import native_enabled, native_module

        if native_enabled("sketch"):
            return native_module().sketch_signature_batch(
                list(texts), mode, k, num_perms, seed
            )
    except Exception:
        pass
    return _signature_batch_python(list(texts), mode, k, num_perms, seed)
