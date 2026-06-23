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

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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
#   - field_scoring: the cdist-shaped per-field score matrix (score_field_matrix
#     -> score-core) behind the default polars-direct path's _fuzzy_score_matrix
#     for jaro_winkler / levenshtein / token_sort / exact / soundex_match. The
#     kernel IS rapidfuzz (same as the pure path); parity asserted to 1e-4 in
#     tests/test_native_field_matrix_parity.py and bit-identical on the
#     levenshtein tracer in the single-kernel-collapse spike (ADR 0016, max abs
#     diff 0.0). This path already preferred the kernel when importable; adding it
#     here (single-kernel-collapse R2) brings that long-standing default UNDER the
#     reversible GOLDENMATCH_NATIVE flag (=0 now actually forces pure) + the
#     dispatch telemetry, with no output change. Bench: kernel not-slower
#     (1.44x faster per-pair) -- the measure-first gate in the spike.
#
# Signed off 2026-06-21 (autoconfig native core -- a SOURCE-OF-TRUTH/consistency
# flip, NOT a perf one; the rest of this list is perf-motivated):
#   - autoconfig: the Layer-1 planner (autoconfig_decide_plan) + Layer-2 column
#     classifier (autoconfig_classify_columns) -- the deterministic auto-config
#     decision logic, ported to the pyo3-free goldenmatch-autoconfig-core crate
#     and shared verbatim by the Python wheel AND the TS port (via wasm). Output
#     is BYTE-IDENTICAL to the pure-Python oracle: 88 golden vectors (49 planner
#     + 39 classifier) generated from pure Python and asserted in
#     tests/test_autoconfig_native_parity.py (+ the Rust tests/golden.rs and the
#     TS tests/parity/autoconfig-core.parity.test.ts), so gating native on never
#     changes a committed config. Distinct rationale from the perf entries above:
#     autoconfig runs ONCE per auto_configure_df (not a per-row hot loop), so this
#     is NOT a wall-clock-lift decision -- it makes the compiled core the single
#     canonical implementation across Python/Rust/TS (repo direction: native is
#     the source of truth wherever the kernel is byte-identical). Published-wheel
#     skew is handled gracefully: both dispatch sites guard with
#     `hasattr(_nm, "autoconfig_decide_plan"/"autoconfig_classify_columns")`, so a
#     wheel that predates the symbols (PyPI 0.1.6) falls through to pure Python
#     until goldenmatch-native 0.1.7 (this change bumps it) is published.
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
#   - "perceptual" (multimodal-ER crawl-tier media hashes, ADR 0022) is shipped
#     native-available but deliberately NOT gated yet, same posture as "pprl_bloom":
#     the published wheel must carry perceptual_phash_image / perceptual_phash_batch
#     / perceptual_fingerprint_audio and a bench must confirm the lift before the
#     default-on flip. Reachable now via GOLDENMATCH_NATIVE=1 (the native<->python
#     parity test forces it). Output is byte-identical (deterministic DCT pHash +
#     Haitsma-Kalker fingerprint, golden-vector verified), so the flip is a
#     perf/wheel-republish decision, not an accuracy one.
_GATED_ON: frozenset[str] = frozenset(
    {
        "clustering",
        "block_scoring",
        "pairs",
        "featurize",
        "hashing",
        "field_scoring",
        "autoconfig",
        # "sketch" (MinHash/LSH + SimHash batch sketching, #1081/#1090) is now
        # default-on -- the same rationale as "autoconfig" above: the compiled
        # sketch-core crate is the single canonical implementation across
        # Python/Rust/TS (repo direction: native is the source of truth wherever
        # the kernel is byte-identical), and the SimHash band-hashing is the
        # candidate-generation kernel under default-on semantic blocking (#1090).
        # Output is byte-identical (deterministic, golden-vector verified -- the
        # native<->python parity test forces both paths) AND measured ~29x faster
        # (5000x128, 16 bands), so this satisfies BOTH the "wheel carries the
        # symbol" and "bench confirms the lift" prerequisites the prior gate
        # named. Published-wheel skew is handled gracefully: the call site in
        # core/sketch.py guards the native symbol and falls through to the pure-
        # Python reference for a wheel predating sketch_simhash_band_hashes_batch.
        "sketch",
    }
)


# Per-process record of which components actually dispatched to the native
# kernel vs fell back to Python, so telemetry can report THIS run's reality
# (#884) instead of just "the wheel is importable + the static allowlist".
# ``native_available()`` being true does NOT mean your workload's hot loop ran
# native -- the component may be off the ``_GATED_ON`` allowlist under ``auto``,
# or (for backend=bucket) the numpy fast-path may have handled the block instead
# of the kernel. This counter answers "did stage X go native on THIS run?".
_DISPATCH_LOG: dict[str, dict[str, int]] = {}
_AUTO_HINT_LOGGED = False


def _record_dispatch(component: str, native: bool) -> None:
    slot = _DISPATCH_LOG.setdefault(component, {"native": 0, "fallback": 0})
    slot["native" if native else "fallback"] += 1


def native_dispatch_report() -> dict[str, dict[str, int]]:
    """Per-component ``{native, fallback}`` dispatch counts for this process.

    Empty until the first ``native_enabled()`` query. Telemetry reads this to
    report which stages ACTUALLY ran native on this run rather than the static
    ``available`` + allowlist (#884). A component with ``native > 0`` dispatched
    to the kernel at least once; ``fallback > 0`` means it ran the Python path
    at least once (off the allowlist, ``GOLDENMATCH_NATIVE=0``, or the wheel
    isn't importable).
    """
    return {k: dict(v) for k, v in _DISPATCH_LOG.items()}


def reset_native_dispatch_log() -> None:
    """Clear the per-process dispatch counters (test isolation / per-run capture)."""
    _DISPATCH_LOG.clear()


def native_module() -> Any:
    """The imported ``goldenmatch._native`` module (typed ``Any`` — its kernels
    are dynamically loaded), or ``None`` if unavailable. Call sites must guard
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call.

    Records the decision (see :func:`native_dispatch_report`) and, the first
    time it runs under ``auto`` with the kernel importable, logs a one-line hint
    that the gated-only allowlist is in effect (set ``GOLDENMATCH_NATIVE=1`` for
    full native acceleration on large / benchmark runs).
    """
    global _AUTO_HINT_LOGGED
    mode = os.environ.get("GOLDENMATCH_NATIVE", "auto").lower()
    if mode == "0":
        _record_dispatch(component, False)
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENMATCH_NATIVE=1 but goldenmatch._native is not built/importable"
            )
        _record_dispatch(component, True)
        return True
    # auto / unset: native iff importable AND signed off (in _GATED_ON).
    if _native is not None and not _AUTO_HINT_LOGGED:
        _AUTO_HINT_LOGGED = True
        logger.info(
            "goldenmatch native kernel available; running under GOLDENMATCH_NATIVE=auto "
            "(gated set only: %s). Set GOLDENMATCH_NATIVE=1 for full native "
            "acceleration on large / benchmark runs.",
            sorted(_GATED_ON),
        )
    result = _native is not None and component in _GATED_ON
    _record_dispatch(component, result)
    return result


# Components that constitute the scoring "hot path" -- the per-field / per-block
# kernels whose dispatch determines throughput. ``field_scoring`` is the
# polars-direct default's cdist-shaped score matrix; ``block_scoring`` is the
# bucket backend's per-block kernel. Whether THESE went native is the question
# #1048 / #957 ask (clustering / hashing / featurize are not throughput-shaped
# scoring, so they don't gate the slow-path warning).
_HOT_PATH_COMPONENTS: frozenset[str] = frozenset({"block_scoring", "field_scoring"})

# Process-level "warn once" guard, keyed by call site, so a distributed run
# doesn't log the slow-path WARNING once per scored batch.
_SLOW_PATH_WARNED: set[str] = set()


@dataclass(frozen=True)
class NativeDispatchSummary:
    """Whether THIS run's scoring dispatched to the native kernel.

    Attached to ``DedupeResult.native`` / ``MatchResult.native`` so a caller can
    *confirm* native dispatch (``hot_path_native``) instead of inferring it from
    wall-clock (#1048). Built from the per-component dispatch counts via
    :func:`summarize_native_dispatch`.

    Attributes:
        available: the native kernel was importable this process.
        mode: the resolved ``GOLDENMATCH_NATIVE`` mode (``auto`` / ``0`` / ``1``).
        components: per-component ``{native, fallback}`` counts for this run.
        ran_native: any component dispatched to the kernel at least once.
        hot_path_exercised: a scoring hot-path component ran at all this run
            (False for e.g. a pure exact-match dedupe with no fuzzy scoring).
        hot_path_native: the scoring hot path ran ENTIRELY on the kernel (every
            hot-path dispatch went native, none fell back). The signal #1048
            wanted: ``True`` means scoring really used the kernel.
    """

    available: bool
    mode: str
    components: dict[str, dict[str, int]] = field(default_factory=dict)
    ran_native: bool = False
    hot_path_exercised: bool = False
    hot_path_native: bool = False

    @property
    def hot_path_native_calls(self) -> int:
        return sum(
            self.components.get(c, {}).get("native", 0) for c in _HOT_PATH_COMPONENTS
        )

    @property
    def hot_path_fallback_calls(self) -> int:
        return sum(
            self.components.get(c, {}).get("fallback", 0) for c in _HOT_PATH_COMPONENTS
        )

    def slow_path_active(self) -> bool:
        """The kernel was importable and scoring ran, but the hot path used the
        pure-Python fallback (wholly or partly). NOT triggered when the user
        forced Python with ``GOLDENMATCH_NATIVE=0`` (that's an explicit choice,
        not a silent slow path)."""
        return (
            self.available
            and self.mode != "0"
            and self.hot_path_exercised
            and not self.hot_path_native
        )

    def __str__(self) -> str:
        comps = ", ".join(
            f"{c} n={v.get('native', 0)}/f={v.get('fallback', 0)}"
            for c, v in sorted(self.components.items())
        )
        return (
            f"native(available={self.available}, mode={self.mode}, "
            f"ran_native={self.ran_native}, hot_path_native={self.hot_path_native}"
            f"{'; ' + comps if comps else ''})"
        )


def summarize_native_dispatch(
    baseline: dict[str, dict[str, int]] | None = None,
) -> NativeDispatchSummary:
    """Build a :class:`NativeDispatchSummary` from the dispatch counters.

    Pass ``baseline`` (a prior :func:`native_dispatch_report` snapshot) to scope
    the summary to the dispatches that happened SINCE that snapshot -- the API
    entry points snapshot just before the full-data pipeline so the summary
    reflects the real dedupe scoring, not the auto-config sample iterations.
    Counters only grow, so the delta is always non-negative.
    """
    current = native_dispatch_report()
    if baseline:
        report: dict[str, dict[str, int]] = {}
        for comp, counts in current.items():
            base = baseline.get(comp, {})
            nat = counts.get("native", 0) - base.get("native", 0)
            fb = counts.get("fallback", 0) - base.get("fallback", 0)
            if nat or fb:
                report[comp] = {"native": nat, "fallback": fb}
    else:
        report = current

    ran_native = any(c.get("native", 0) > 0 for c in report.values())
    hot = {c: report[c] for c in _HOT_PATH_COMPONENTS if c in report}
    hot_exercised = any(
        (v.get("native", 0) + v.get("fallback", 0)) > 0 for v in hot.values()
    )
    # Fully native iff every hot-path component that ran went native with no
    # fallback. A single fallback (an ungated/uncompiled scorer) flips this off.
    hot_native = hot_exercised and all(
        v.get("fallback", 0) == 0 and v.get("native", 0) > 0 for v in hot.values()
    )
    mode = os.environ.get("GOLDENMATCH_NATIVE", "auto").lower()
    return NativeDispatchSummary(
        available=native_available(),
        mode=mode,
        components=report,
        ran_native=ran_native,
        hot_path_exercised=hot_exercised,
        hot_path_native=hot_native,
    )


def warn_if_slow_path(
    summary: NativeDispatchSummary,
    log: logging.Logger | None = None,
    *,
    once_key: str | None = None,
) -> bool:
    """Log a WARNING when ``summary.slow_path_active()`` -- the kernel is
    importable but scoring ran on the pure-Python fallback (#957: never silently
    eat the slow path; #1048: make the slow path diagnosable). Returns whether a
    warning was emitted. ``once_key`` de-dupes per process (used by the
    distributed workers so each logs at most once)."""
    if not summary.slow_path_active():
        return False
    if once_key is not None:
        if once_key in _SLOW_PATH_WARNED:
            return False
        _SLOW_PATH_WARNED.add(once_key)
    (log or logger).warning(
        "goldenmatch native kernel is importable but the scoring hot path ran "
        "on the pure-Python fallback this run (hot-path native=%d, fallback=%d). "
        "Throughput is the slow path. Likely causes: a matchkey scorer with no "
        "native kernel, or a component off the GOLDENMATCH_NATIVE=auto allowlist "
        "-- set GOLDENMATCH_NATIVE=1 to require native on every supported "
        "component. Inspect result.native for the per-component breakdown.",
        summary.hot_path_native_calls,
        summary.hot_path_fallback_calls,
    )
    return True


def reset_slow_path_warned() -> None:
    """Clear the per-process slow-path warn-once guard (test isolation)."""
    _SLOW_PATH_WARNED.clear()
