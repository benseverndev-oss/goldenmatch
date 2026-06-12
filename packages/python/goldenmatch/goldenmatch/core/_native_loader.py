"""Loader + gate for the optional ``goldenmatch._native`` acceleration module.

The native extension (Rust/PyO3, built from ``packages/rust/extensions/native``)
is an *optional accelerator*: when it isn't importable, the pure-Python paths run
unchanged. Selection is centralised here so every call site reads one gate.

``GOLDENMATCH_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native iff it's importable AND the component has been
  signed off (is in ``_GATED_ON``). ``clustering`` and ``block_scoring`` have
  cleared the gate (DQbench ER composite unchanged vs the pure-Python baseline),
  so under ``auto`` they run native whenever the ext is importable — we ship the
  ext able to run and flip the default per phase, per the spec.

Spec: ``docs/design/2026-05-25-rust-acceleration-spec.md`` §0.3.
"""
from __future__ import annotations

import os
from typing import Any

# The kernel is reachable two ways, tried in order:
#   1. ``goldenmatch._native`` — the in-tree build dropped by
#      ``scripts/build_native.py`` for local dev / the CI parity lane.
#   2. ``goldenmatch_native._native`` — the separately-distributed
#      ``goldenmatch-native`` abi3 wheel (``pip install goldenmatch[native]``),
#      the polars / polars-runtime split. Same ``_native`` pymodule either way.
try:
    import goldenmatch._native as _native  # pyright: ignore[reportMissingImports]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenmatch_native import _native  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Components whose native path has cleared parity + DQbench and may run under
# ``GOLDENMATCH_NATIVE=auto``. Add a name here only after sign-off.
#
# Signed off 2026-05-25 (DQbench ER native-parity validation):
#   - clustering: exercised end-to-end across DQbench ER T1-T4 (polars-direct
#     backend); composite identical native-vs-python (92.03), all per-tier
#     P/R/F1/TP/FP/FN equal.
#   - block_scoring: not reached by DQbench (controller picks polars-direct at
#     these sizes); validated separately on a forced bucket-backend workload —
#     emitted pair set identical, scores within 1 ULP (no threshold crossings).
#   - pairs: the Native Core primitives (canonicalize_pairs,
#     dedup_pairs_max_score, candidate_pair_count, block_histogram,
#     connected_components). Bit-exact with the Python reference — integer
#     arithmetic plus a strict-`>` max reduction, no float tolerance — so there
#     is no threshold-crossing risk and no DQbench surface to clear; parity is
#     asserted directly in tests/test_native_parity.py.
#   - featurize: the in-house embedder's char-n-gram feature hashing
#     (CharNGramFeaturizer). BLAKE2b hashing + float32 normalization match the
#     Python reference bit-for-bit (the nonzero counts are small exact integers,
#     so the sum-of-squares carries no rounding); parity asserted in
#     tests/test_native_parity.py.
#   - hashing: the canonical record_fingerprint kernel. SHA-256 over an
#     identical type-tagged, key-sorted, framed byte canonicalization in Rust
#     and Python -- asserted byte-for-byte (incl. pinned golden vectors) in
#     tests/test_record_fingerprint.py. Native vs Python produce the same id,
#     so gating native on/off never changes a record id.
#
# NOT yet gated on (ships default-off; reachable via GOLDENMATCH_NATIVE=1):
#   - pprl_bloom: the CLK bloom-filter hash loop (bloom_clk_batch). Python does
#     all preprocessing (lower/strip/pad/balanced-salt) and the kernel only
#     re-slices the prepared string by code point + runs the SHA-256/HMAC double
#     loop, so output is byte-identical hex. Parity asserted in
#     tests/test_native_bloom_parity.py. Bit-exact (set bits, no float), so
#     gating on/off never changes a CLK or a dice/jaccard score. Add "pprl_bloom"
#     to _GATED_ON only after the parity battery is green on the PUBLISHED wheel
#     (a republish must ship the new symbol -- see the goldenmatch-native wheel-
#     skew note in the root CLAUDE.md) and a wall-clock bench confirms the lift.
_GATED_ON: frozenset[str] = frozenset(
    {"clustering", "block_scoring", "pairs", "featurize", "hashing"}
)


def native_module() -> Any:
    """The imported ``goldenmatch._native`` module (typed ``Any`` — its kernels
    are dynamically loaded), or ``None`` if unavailable. Call sites must guard
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call."""
    mode = os.environ.get("GOLDENMATCH_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENMATCH_NATIVE=1 but goldenmatch._native is not built/importable"
            )
        return True
    return _native is not None and component in _GATED_ON
