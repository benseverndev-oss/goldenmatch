"""Scorers as Spark pandas UDFs for the Sail tier (Spark Connect).

Two backends, identical [0, 1] semantics:

- **PURE floor** -- goldenmatch's OWN ``core.strsim`` (bit-parallel LCS/Jaro/
  Myers), the exact parity reference. Always available; no native wheel and
  NO third-party scorer dependency needed -- rapidfuzz is a dev-only extra
  and is not installed at runtime.
- **NATIVE** -- the shared `score-core` kernel via the vectorized
  `score_field_pairwise` Arrow UDF: one FFI crossing per batch, Arrow zero-copy,
  scored row-parallel in Rust (no per-element Python loop, no N*N matrix). This is
  the R1 "native Arrow UDF" target from the past-one-box roadmap -- benching the
  pure floor measures Python-UDF overhead, not the engine.

Backend selection rides the one reversible flag (`native_enabled("sail_scoring")`):
it ships **default-off** (component not in the loader's `_GATED_ON` allowlist, so
`GOLDENMATCH_NATIVE=auto`/unset -> pure; `GOLDENMATCH_NATIVE=1` -> native). Promote
`sail_scoring` into `_GATED_ON` only after the parity battery is green on the
PUBLISHED wheel (the loader's documented rule). The native path falls back to pure
per-batch on any FFI/pyarrow hiccup, so a worker without the wheel still scores
correctly -- just slower. Native returns **f64**, deliberately unlike
`score_field_matrix` / the DataFusion FFI scorer's f32: `score_one` computes in
f64 and the old narrowing cast alone changed MATCH DECISIONS at round thresholds
(a pure 0.95 became 0.949999988, so `>= 0.95` flipped on ordinary surname pairs).
A tolerance is fine for a score you report and not for one you THRESHOLD. See
spec 2026-08-10-spark-native-execution-design section 6."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# SQL-callable names (match the matchkey scorer names the spine supports).
_SUPPORTED = ("jaro_winkler", "levenshtein", "token_sort")

# Native field-scorer ids -- mirror score.rs::score_field_pairwise (0..=3) and
# _native_field_matrix's _NATIVE_FIELD_SCORER_IDS.
_NATIVE_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0,
    "levenshtein": 1,
    "token_sort": 2,
}


_NATIVE_SCORER_F32_WARNED = False


def _warn_native_scorer_wheel_too_old_once(dtype: Any) -> None:
    """Warn once when the installed native wheel returns narrow floats.

    goldenmatch-native < 0.1.21 narrowed `score_field_pairwise` to f32, which
    changes match decisions at round thresholds. We fall back to the pure floor
    (correct, slower) rather than take the faster wrong answer -- but silence
    here would look exactly like "native is not installed", so say it once.
    """
    global _NATIVE_SCORER_F32_WARNED
    if _NATIVE_SCORER_F32_WARNED:
        return
    _NATIVE_SCORER_F32_WARNED = True
    logger.warning(
        "goldenmatch-native returns %s from score_field_pairwise; native Spark "
        "scoring is DISABLED for this session because narrow floats change match "
        "decisions at round thresholds. Upgrade to goldenmatch-native>=0.1.21 "
        "for the f64 kernel.",
        dtype,
    )


def _pure_scores(scorer_name: str, a: Any, b: Any) -> list[float]:
    """The pure-Python rapidfuzz floor + parity reference. ``a``/``b`` are
    iterables of ``str | None`` (None scored as ""); returns list[float] in
    [0, 1]."""
    from goldenmatch.core import strsim

    if scorer_name == "jaro_winkler":
        def score(x: Any, y: Any) -> float:
            return strsim.jaro_winkler_normalized_similarity(x or "", y or "")
    elif scorer_name == "levenshtein":
        def score(x: Any, y: Any) -> float:
            return strsim.levenshtein_normalized_similarity(x or "", y or "")
    elif scorer_name == "token_sort":
        # repo convention: normalize the 0-100 ratio scorer to [0, 1].
        def score(x: Any, y: Any) -> float:
            return strsim.token_sort_ratio(x or "", y or "") / 100.0
    else:
        raise NotImplementedError(
            f"Sail supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    return [score(x, y) for x, y in zip(a, b)]


def _native_scores(scorer_name: str, a: Any, b: Any) -> Any | None:
    """The native `score_field_pairwise` path. Returns an ``np.ndarray`` of
    float64 in [0, 1], or ``None`` when the native kernel isn't enabled /
    importable / present -- caller falls back to the pure floor."""
    scorer_id = _NATIVE_SCORER_IDS.get(scorer_name)
    if scorer_id is None:
        return None
    try:
        from goldenmatch.core._native_loader import native_enabled, native_module

        if not native_enabled("sail_scoring"):
            return None
        native = native_module()
    except Exception:
        return None
    if native is None or not hasattr(native, "score_field_pairwise"):
        return None
    try:
        import pyarrow as pa

        aa = pa.array(list(a), type=pa.large_string())
        bb = pa.array(list(b), type=pa.large_string())
        out = native.score_field_pairwise(aa, bb, scorer_id)
    except Exception:
        # Any FFI / pyarrow hiccup falls through to the pure floor.
        return None

    # BEHAVIOUR guard, not a symbol guard. `score_field_pairwise` exists on the
    # f32 goldenmatch-native <= 0.1.20 wheels as well, so the loader's
    # symbol-presence check cannot tell them apart -- and an f32 result changes
    # MATCH DECISIONS at round thresholds (a pure 0.95 arrives as 0.949999988,
    # so `>= 0.95` flips; measured on ordinary surname pairs). An older installed
    # wheel must degrade to the pure floor rather than silently reintroduce that.
    dtype = getattr(out, "dtype", None)
    if dtype is not None and getattr(dtype, "itemsize", 0) < 8:
        _warn_native_scorer_wheel_too_old_once(dtype)
        return None
    return out


def score_batch(scorer_name: str, a: Any, b: Any) -> Any:
    """Backend-agnostic elementwise batch scorer: native when enabled +
    available, else the pure floor. Returns a sequence of float in [0, 1].

    The single-process entry point the parity test + bench exercise (no Spark
    needed)."""
    if scorer_name not in _SUPPORTED:
        raise NotImplementedError(
            f"Sail supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    out = _native_scores(scorer_name, a, b)
    if out is not None:
        return out
    return _pure_scores(scorer_name, a, b)


def make_scorer_udf(scorer_name: str) -> Any:
    """Return a Spark ``pandas_udf`` (double) scoring two string columns in
    [0, 1] -- native Arrow kernel when enabled, pure rapidfuzz otherwise."""
    if scorer_name not in _SUPPORTED:
        raise NotImplementedError(
            f"Sail supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("double")
    def _udf(a, b):  # a, b: pandas Series[str]
        import numpy as np
        import pandas as pd

        scores = score_batch(scorer_name, a, b)
        return pd.Series(np.asarray(scores, dtype="float64"), index=a.index)

    return _udf
