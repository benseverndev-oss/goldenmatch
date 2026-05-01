"""Correlation analyzer — Pearson (numeric-numeric) and Cramer's V (categorical-categorical)."""
from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING

try:
    import numpy as np
    from scipy.stats import chi2_contingency, pearsonr
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "scipy and numpy are required for deep-profiling baseline. "
        "Install them with: pip install 'goldencheck[baseline]'"
    ) from _err

import polars as pl

from goldencheck.baseline.models import CorrelationEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = ["analyze_correlations", "_cramers_v"]

# Threshold constants
_STRONG_THRESHOLD = 0.7
_MODERATE_THRESHOLD = 0.4

# Maximum number of column pairs to evaluate (prevents O(n²) blowup on wide datasets)
_MAX_PAIRS = 500

# Minimum non-null rows required per pair
_MIN_ROWS = 30

# Maximum unique values for a string column to be treated as categorical
_MAX_CAT_UNIQUE = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strength(value: float) -> str:
    """Classify absolute correlation value into 'strong', 'moderate', or 'weak'."""
    abs_val = abs(value)
    if abs_val >= _STRONG_THRESHOLD:
        return "strong"
    if abs_val >= _MODERATE_THRESHOLD:
        return "moderate"
    return "weak"


def _pearson_entry(df: pl.DataFrame, col_a: str, col_b: str) -> CorrelationEntry | None:
    """Compute Pearson correlation between two numeric columns, or return None."""
    sub = df.select([col_a, col_b]).drop_nulls()
    if len(sub) < _MIN_ROWS:
        return None

    a_vals = sub[col_a].to_numpy()
    b_vals = sub[col_b].to_numpy()

    # Guard against zero-variance columns (pearsonr would raise or return NaN)
    if np.std(a_vals) == 0.0 or np.std(b_vals) == 0.0:
        return None

    corr, _ = pearsonr(a_vals, b_vals)
    if not np.isfinite(corr):
        return None

    strength = _strength(corr)
    if strength == "weak":
        return None

    return CorrelationEntry(
        columns=sorted([col_a, col_b]),
        measure="pearson",
        value=round(float(corr), 6),
        strength=strength,
        note=None,
    )


def _cramers_v(df: pl.DataFrame, col_a: str, col_b: str) -> CorrelationEntry | None:
    """Compute Cramer's V between two categorical columns, or return None.

    This function is exposed at module level for use by drift detectors.
    """
    sub = df.select([col_a, col_b]).drop_nulls()
    if len(sub) < _MIN_ROWS:
        return None

    # Build contingency table via group_by + pivot
    try:
        contingency = (
            sub.group_by([col_a, col_b])
            .agg(pl.len().alias("_cnt"))
            .pivot(on=col_b, index=col_a, values="_cnt")
            .fill_null(0)
        )
    except Exception as exc:
        logger.debug("Contingency table failed for (%s, %s): %s", col_a, col_b, exc)
        return None

    # Extract numeric matrix (drop the index column)
    index_col = col_a
    value_cols = [c for c in contingency.columns if c != index_col]
    if not value_cols:
        return None

    matrix = contingency.select(value_cols).to_numpy()
    if matrix.shape[0] < 2 or matrix.shape[1] < 2:
        return None

    try:
        chi2, _, _, _ = chi2_contingency(matrix)
    except Exception as exc:
        logger.debug("Chi2 contingency failed for (%s, %s): %s", col_a, col_b, exc)
        return None

    n = int(matrix.sum())
    if n == 0:
        return None

    # Cramer's V formula with bias correction (Bergsma & Wicher, 2013)
    r, k = matrix.shape
    phi2 = chi2 / n
    phi2_corr = max(0.0, phi2 - (k - 1) * (r - 1) / (n - 1))
    r_corr = r - (r - 1) ** 2 / (n - 1)
    k_corr = k - (k - 1) ** 2 / (n - 1)
    denom = min(k_corr - 1, r_corr - 1)
    if denom <= 0:
        return None

    v = float(np.sqrt(phi2_corr / denom))
    if not np.isfinite(v):
        return None

    strength = _strength(v)
    if strength == "weak":
        return None

    return CorrelationEntry(
        columns=sorted([col_a, col_b]),
        measure="cramers_v",
        value=round(v, 6),
        strength=strength,
        note=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_correlations(df: pl.DataFrame) -> list[CorrelationEntry]:
    """Analyze pairwise correlations in *df* and return reportable entries.

    - Numeric pairs: Pearson correlation via scipy.stats.pearsonr.
    - Categorical pairs: Cramer's V via chi2_contingency.
    - Only moderate (>=0.4) or strong (>=0.7) correlations are reported.
    - At most _MAX_PAIRS column pairs are evaluated to prevent O(n²) blowup.
    - String columns must have n_unique < _MAX_CAT_UNIQUE to qualify as categorical.
    - Each pair must have at least _MIN_ROWS non-null rows.
    """
    # Partition columns by type
    numeric_cols: list[str] = [
        c for c in df.columns if df[c].dtype in (pl.Float32, pl.Float64, pl.Int8, pl.Int16,
                                                  pl.Int32, pl.Int64, pl.UInt8, pl.UInt16,
                                                  pl.UInt32, pl.UInt64)
    ]
    categorical_cols: list[str] = [
        c for c in df.columns
        if df[c].dtype in (pl.Utf8, pl.String, pl.Categorical)
        and df[c].n_unique() < _MAX_CAT_UNIQUE
    ]

    results: list[CorrelationEntry] = []

    # --- Numeric-numeric pairs ---
    num_pairs = list(itertools.combinations(numeric_cols, 2))
    for col_a, col_b in num_pairs[:_MAX_PAIRS]:
        entry = _pearson_entry(df, col_a, col_b)
        if entry is not None:
            results.append(entry)

    # --- Categorical-categorical pairs ---
    num_evaluated = min(len(num_pairs), _MAX_PAIRS)
    remaining_budget = _MAX_PAIRS - num_evaluated
    if remaining_budget > 0:
        cat_pairs = list(itertools.combinations(categorical_cols, 2))
        for col_a, col_b in cat_pairs[:remaining_budget]:
            entry = _cramers_v(df, col_a, col_b)
            if entry is not None:
                results.append(entry)

    return results
