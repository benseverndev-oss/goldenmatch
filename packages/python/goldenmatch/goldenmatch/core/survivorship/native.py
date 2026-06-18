"""Vectorized survivorship resolution (provenance=False fast path).

Byte-identical to the slow per-cluster path (the oracle). Built up
strategy-by-strategy, each gated by a parity test in test_native_parity.py.
"""
from __future__ import annotations


def survivorship_native_eligible(rules, provenance) -> bool:
    """True when the vectorized survivorship path can handle this config.
    Returns False for now -- flipped on in Phase F when the path is complete."""
    return False


def build_survivorship_native(rules_unused=None, *args, **kwargs):
    raise NotImplementedError("build_survivorship_native implemented in Phase B+")
