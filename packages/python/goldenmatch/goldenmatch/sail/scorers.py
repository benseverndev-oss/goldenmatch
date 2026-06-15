"""Scorers as Spark pandas UDFs for the Sail tier (Spark Connect).

Two backends, identical [0, 1] semantics:

- **PURE floor** -- pure-Python rapidfuzz (rust-rapidfuzz == python-rapidfuzz at
  1e-9), the exact parity reference. Always available; no native wheel needed.
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
correctly -- just slower. Native returns f32 (the repo convention for the field
scorers, matching `score_field_matrix` / the DataFusion FFI scorer); parity vs the
pure f64 floor holds to f32 epsilon."""
from __future__ import annotations

from typing import Any

# SQL-callable names (match the matchkey scorer names the spine supports).
_SUPPORTED = ("jaro_winkler", "levenshtein", "token_sort")

# Native field-scorer ids -- mirror score.rs::score_field_pairwise (0..=3) and
# _native_field_matrix's _NATIVE_FIELD_SCORER_IDS.
_NATIVE_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0,
    "levenshtein": 1,
    "token_sort": 2,
}


def _pure_scores(scorer_name: str, a: Any, b: Any) -> list[float]:
    """The pure-Python rapidfuzz floor + parity reference. ``a``/``b`` are
    iterables of ``str | None`` (None scored as ""); returns list[float] in
    [0, 1]."""
    from rapidfuzz import fuzz
    from rapidfuzz.distance import JaroWinkler, Levenshtein

    if scorer_name == "jaro_winkler":
        def score(x: Any, y: Any) -> float:
            return JaroWinkler.normalized_similarity(x or "", y or "")
    elif scorer_name == "levenshtein":
        def score(x: Any, y: Any) -> float:
            return Levenshtein.normalized_similarity(x or "", y or "")
    elif scorer_name == "token_sort":
        # repo convention: normalize the 0-100 ratio scorer to [0, 1].
        def score(x: Any, y: Any) -> float:
            return fuzz.token_sort_ratio(x or "", y or "") / 100.0
    else:
        raise NotImplementedError(
            f"Sail supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    return [score(x, y) for x, y in zip(a, b)]


def _native_scores(scorer_name: str, a: Any, b: Any) -> Any | None:
    """The native `score_field_pairwise` path. Returns an ``np.ndarray`` of
    float32 in [0, 1], or ``None`` when the native kernel isn't enabled /
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
        return native.score_field_pairwise(aa, bb, scorer_id)
    except Exception:
        # Any FFI / pyarrow hiccup falls through to the pure floor.
        return None


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
