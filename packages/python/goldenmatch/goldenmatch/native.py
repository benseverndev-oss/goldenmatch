"""Public Native Core surface.

Stable import path for the native-runtime primitives::

    from goldenmatch.native import (
        canonicalize_pairs,
        dedup_pairs_max_score,
        connected_components,
        candidate_pair_count,
        block_histogram,
    )

Each function has a pure-Python implementation (in ``core/pairs.py``) that is the
source of truth and an optional Rust kernel that accelerates it when the
``goldenmatch._native`` extension is built. ``available()`` reports whether the
compiled accelerator is importable; the primitives work either way.
"""
from __future__ import annotations

from goldenmatch.core._native_loader import native_available as available
from goldenmatch.core.pairs import (
    block_histogram,
    candidate_pair_count,
    canonicalize_pairs,
    connected_components,
    dedup_pairs_max_score,
)

__all__ = [
    "available",
    "block_histogram",
    "candidate_pair_count",
    "canonicalize_pairs",
    "connected_components",
    "dedup_pairs_max_score",
]
