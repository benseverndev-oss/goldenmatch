"""Public Native Core surface.

Stable import path for the native-runtime primitives::

    from goldenmatch.native import (
        canonicalize_pairs,
        dedup_pairs_max_score,
        connected_components,
        candidate_pair_count,
        block_histogram,
    )

Each primitive has a pure-Python implementation (the source of truth) and an
optional Rust kernel that accelerates it when the ``goldenmatch._native``
extension is built. ``available()`` reports whether the compiled accelerator is
importable; everything here works either way (it falls back to pure Python /
rapidfuzz). ``string_similarity`` exposes the native string scorers directly::

    from goldenmatch.native import string_similarity, string_similarity as sim
    sim("John Smith", "Jon Smyth", "jaro_winkler")
"""
from __future__ import annotations

from goldenmatch.core._native_loader import native_available as available
from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core.pairs import (
    block_histogram,
    candidate_pair_count,
    canonicalize_pairs,
    connected_components,
    dedup_pairs_max_score,
)

STRING_SCORERS = ("jaro_winkler", "levenshtein", "token_sort")


def string_similarity(a: str | None, b: str | None, scorer: str = "jaro_winkler") -> float:
    """Native-accelerated string similarity in ``[0, 1]``.

    ``scorer`` is one of :data:`STRING_SCORERS`. Uses the Rust kernel when the
    ``goldenmatch._native`` extension is importable (bit-parity with rapidfuzz to
    1e-9), else the pure-Python rapidfuzz reference. ``None`` is treated as the
    empty string. The gate honours ``GOLDENMATCH_NATIVE`` like every other native
    call (these are the kernels behind native block scoring)."""
    if scorer not in STRING_SCORERS:
        raise ValueError(f"scorer must be one of {STRING_SCORERS}, got {scorer!r}")
    sa = "" if a is None else str(a)
    sb = "" if b is None else str(b)
    if native_enabled("block_scoring"):
        mod = native_module()
        if scorer == "jaro_winkler":
            return float(mod.jaro_winkler_similarity(sa, sb))
        if scorer == "levenshtein":
            return float(mod.levenshtein_similarity(sa, sb))
        return float(mod.token_sort_ratio(sa, sb)) / 100.0
    # Pure-Python reference — exactly what the native kernels replicate.
    from rapidfuzz.distance import JaroWinkler, Levenshtein
    from rapidfuzz.fuzz import token_sort_ratio
    if scorer == "jaro_winkler":
        return float(JaroWinkler.similarity(sa, sb))
    if scorer == "levenshtein":
        return float(Levenshtein.normalized_similarity(sa, sb))
    return float(token_sort_ratio(sa, sb)) / 100.0


__all__ = [
    "STRING_SCORERS",
    "available",
    "block_histogram",
    "candidate_pair_count",
    "canonicalize_pairs",
    "connected_components",
    "dedup_pairs_max_score",
    "string_similarity",
]
