"""Scorers as Spark pandas UDFs (S1: pure-Python rapidfuzz -- the floor +
parity reference; the native-kernel Arrow UDF is a later perf task).

The same rapidfuzz the one-box spine's FFI scorer delegates to
(rust-rapidfuzz == python-rapidfuzz at 1e-9), so this is the exact parity
reference, not an approximation."""
from __future__ import annotations

from typing import Any

# SQL-callable names (match the matchkey scorer names the spine supports).
_SUPPORTED = ("jaro_winkler", "levenshtein", "token_sort")


def make_scorer_udf(scorer_name: str) -> Any:
    """Return a Spark ``pandas_udf`` (double) scoring two string columns in
    [0, 1] via rapidfuzz."""
    if scorer_name not in _SUPPORTED:
        raise NotImplementedError(
            f"Sail S1 supports scorers {_SUPPORTED}; got {scorer_name!r}."
        )
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("double")
    def _udf(a, b):  # a, b: pandas Series[str]
        import pandas as pd
        from rapidfuzz import fuzz
        from rapidfuzz.distance import JaroWinkler, Levenshtein

        def score(x: str, y: str) -> float:
            x = x or ""
            y = y or ""
            if scorer_name == "jaro_winkler":
                return JaroWinkler.normalized_similarity(x, y)
            if scorer_name == "levenshtein":
                return Levenshtein.normalized_similarity(x, y)
            # token_sort: rapidfuzz fuzz.token_sort_ratio / 100 (repo convention
            # for normalizing the 0-100 ratio scorers to [0, 1]).
            return fuzz.token_sort_ratio(x, y) / 100.0

        return pd.Series([score(x, y) for x, y in zip(a, b)])

    return _udf
