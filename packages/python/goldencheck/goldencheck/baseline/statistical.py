"""Statistical profiler — distributions, Benford's law, entropy, percentile bounds."""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import TYPE_CHECKING

try:
    import numpy as np
    import scipy.stats as _stats
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "scipy and numpy are required for deep-profiling baseline. "
        "Install them with: pip install 'goldencheck[baseline]'"
    ) from _err

import polars as pl

from goldencheck.baseline.models import StatProfile

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = ["profile_statistical"]

# Minimum number of non-null rows required to profile a column.
_MIN_ROWS = 30

# Column-name keywords that make a column eligible for Benford's law.
_BENFORD_KEYWORDS = frozenset(
    {"amount", "total", "revenue", "population", "count", "price", "salary", "income", "cost", "fee"}
)

# Keyword fragments that mark a column as an identifier/code — Benford skipped.
_ID_KEYWORDS = frozenset({"_id", "id_", " id", "id ", "code", "key", "uuid", "guid", "hash", "ref"})

# Keyword fragments that mark a column as a percentage — Benford skipped.
_PCT_KEYWORDS = frozenset({"pct", "percent", "ratio", "rate", "share", "proportion"})

# Candidate distributions for fitting, each as (name, scipy_dist).
_CANDIDATE_DISTS: list[tuple[str, object]] = [
    ("normal", _stats.norm),
    ("log_normal", _stats.lognorm),
    ("exponential", _stats.expon),
    ("uniform", _stats.uniform),
]

# Minimum KS-test p-value to accept a distribution fit.
_KS_MIN_PVALUE = 0.01


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def profile_statistical(
    df: pl.DataFrame,
    semantic_types: dict[str, list[str]] | None = None,
) -> dict[str, StatProfile]:
    """Compute a :class:`~goldencheck.baseline.models.StatProfile` for each column.

    Parameters
    ----------
    df:
        Input Polars DataFrame.
    semantic_types:
        Optional mapping of ``column_name -> [type_tag, ...]`` (e.g.
        ``{"revenue": ["amount", "currency"]}``).  Used to inform Benford
        eligibility.

    Returns
    -------
    dict[str, StatProfile]
        Mapping from column name to its statistical profile.  Columns with
        fewer than ``_MIN_ROWS`` non-null values are omitted.
    """
    sem = semantic_types or {}
    profiles: dict[str, StatProfile] = {}

    for col in df.columns:
        series = df[col]
        non_null = series.drop_nulls()
        if len(non_null) < _MIN_ROWS:
            continue

        is_numeric = series.dtype in (
            pl.Int8, pl.Int16, pl.Int32, pl.Int64,
            pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
            pl.Float32, pl.Float64,
        )

        if is_numeric:
            profiles[col] = _profile_numeric(col, non_null, sem.get(col, []))
        else:
            profiles[col] = _profile_categorical(col, non_null)

    return profiles


# ---------------------------------------------------------------------------
# Numeric profiling
# ---------------------------------------------------------------------------


def _profile_numeric(
    col: str,
    series: pl.Series,
    sem_tags: list[str],
) -> StatProfile:
    """Build a StatProfile for a numeric column."""
    values: np.ndarray = series.cast(pl.Float64).to_numpy()
    values = values[np.isfinite(values)]

    distribution, params = _fit_distribution(values)
    entropy = _histogram_entropy(values)
    bounds = _numeric_bounds(values)
    benford = _maybe_benford(col, values, sem_tags)

    return StatProfile(
        distribution=distribution,
        params=params,
        benford=benford,
        entropy=entropy,
        bounds=bounds,
    )


def _fit_distribution(values: np.ndarray) -> tuple[str | None, dict | None]:
    """Fit candidate distributions; return the best fit by AIC (penalises extra params).

    A candidate is only considered if its KS-test p-value >= ``_KS_MIN_PVALUE``.
    Returns ``(None, None)`` if no distribution achieves that threshold.

    Using AIC rather than raw p-value avoids preferring log_normal over normal
    for genuinely normal data simply because log_normal has a marginally higher
    p-value but uses an extra parameter.
    """
    best_name: str | None = None
    best_params: dict | None = None
    best_aic: float = math.inf

    for name, dist in _CANDIDATE_DISTS:
        # log_normal requires strictly positive values
        if name == "log_normal" and np.any(values <= 0):
            continue
        # exponential requires non-negative values
        if name == "exponential" and np.any(values < 0):
            continue

        try:
            fit_params = dist.fit(values)  # type: ignore[attr-defined]
            _stat, pvalue = _stats.kstest(values, dist.name, args=fit_params)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("Distribution fit failed for %s: %s", name, exc)
            continue

        if pvalue < _KS_MIN_PVALUE:
            continue  # Not a statistically acceptable fit

        try:
            loglik = float(np.sum(dist.logpdf(values, *fit_params)))  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("Log-likelihood computation failed for %s: %s", name, exc)
            continue

        k = len(fit_params)
        aic = 2 * k - 2 * loglik

        if aic < best_aic:
            best_aic = aic
            best_name = name
            best_params = _params_to_dict(name, fit_params)

    if best_name is None:
        return None, None

    return best_name, best_params


def _params_to_dict(dist_name: str, fit_params: tuple) -> dict[str, float]:
    """Convert scipy fit params tuple to a human-readable dict."""
    if dist_name == "normal":
        loc, scale = fit_params
        return {"loc": float(loc), "scale": float(scale)}
    if dist_name == "log_normal":
        # scipy lognorm: (s, loc, scale) — s=shape, scale=exp(mean)
        s, loc, scale = fit_params
        return {"s": float(s), "loc": float(loc), "scale": float(scale)}
    if dist_name == "exponential":
        loc, scale = fit_params
        return {"loc": float(loc), "scale": float(scale)}
    if dist_name == "uniform":
        loc, scale = fit_params
        return {"loc": float(loc), "scale": float(scale)}
    # Generic fallback
    return {f"p{i}": float(v) for i, v in enumerate(fit_params)}


def _histogram_entropy(values: np.ndarray) -> float:
    """Compute approximate Shannon entropy using a histogram of the values."""
    n = len(values)
    if n == 0:
        return 0.0
    # Sturges' rule for bin count, capped for performance
    n_bins = max(10, min(100, int(math.ceil(math.log2(n) + 1))))
    counts, _ = np.histogram(values, bins=n_bins)
    probs = counts[counts > 0] / n
    return float(-np.sum(probs * np.log2(probs)))


def _numeric_bounds(values: np.ndarray) -> dict[str, float]:
    """Return min, max, p01, p99 bounds for a numeric array."""
    p01, p99 = np.percentile(values, [1, 99])
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p01": float(p01),
        "p99": float(p99),
    }


# ---------------------------------------------------------------------------
# Categorical profiling
# ---------------------------------------------------------------------------


def _profile_categorical(col: str, series: pl.Series) -> StatProfile:
    """Build a StatProfile for a non-numeric (categorical/string) column."""
    values = series.cast(pl.Utf8).to_list()
    entropy = _categorical_entropy(values)
    n_unique = series.n_unique()
    bounds: dict = {"n_unique": int(n_unique)}
    return StatProfile(
        distribution=None,
        params=None,
        benford=None,
        entropy=entropy,
        bounds=bounds,
    )


def _categorical_entropy(values: list) -> float:
    """Compute Shannon entropy (bits) for a list of categorical values."""
    n = len(values)
    if n == 0:
        return 0.0
    counts = Counter(values)
    entropy = 0.0
    for cnt in counts.values():
        p = cnt / n
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


# ---------------------------------------------------------------------------
# Benford's law
# ---------------------------------------------------------------------------


def _maybe_benford(
    col: str,
    values: np.ndarray,
    sem_tags: list[str],
) -> dict[str, float] | None:
    """Return Benford leading-digit frequencies (and chi-sq p-value) or None.

    Eligibility rules:
    - All values must be non-negative.
    - Values must span at least 2 orders of magnitude.
    - Column name must contain a Benford keyword OR semantic type contains
      amount/currency/count.
    - Column name must NOT contain identifier/code or percentage keywords.
    """
    col_lower = col.lower()

    # Skip identifier and percentage columns
    if any(kw in col_lower for kw in _ID_KEYWORDS):
        return None
    if any(kw in col_lower for kw in _PCT_KEYWORDS):
        return None

    # Check keyword or semantic eligibility
    name_eligible = any(kw in col_lower for kw in _BENFORD_KEYWORDS)
    sem_eligible = any(tag in {"amount", "currency", "count"} for tag in sem_tags)
    if not name_eligible and not sem_eligible:
        return None

    # Require non-negative values
    pos = values[values > 0]
    if len(pos) < _MIN_ROWS:
        return None

    # Require span of 2+ orders of magnitude
    span = np.log10(np.max(pos)) - np.log10(np.min(pos))
    if span < 2.0:
        return None

    return _compute_benford(pos)


def _compute_benford(values: np.ndarray) -> dict[str, float]:
    """Compute observed leading-digit frequencies and chi-squared p-value."""
    leading_digits = _extract_leading_digits(values)
    total = len(leading_digits)
    observed_counts = Counter(leading_digits)

    # Benford expected proportions for digits 1-9
    expected_props = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

    result: dict[str, float] = {}
    observed_props: list[float] = []
    expected_vals: list[float] = []

    for d in range(1, 10):
        obs_count = observed_counts.get(d, 0)
        obs_prop = obs_count / total if total > 0 else 0.0
        result[str(d)] = round(obs_prop, 6)
        observed_props.append(obs_count)
        expected_vals.append(expected_props[d] * total)

    # Chi-squared test
    chi2, pvalue = _stats.chisquare(f_obs=observed_props, f_exp=expected_vals)
    result["chi2_pvalue"] = round(float(pvalue), 6)

    return result


def _extract_leading_digits(values: np.ndarray) -> list[int]:
    """Extract the leading significant digit (1-9) from each value."""
    digits: list[int] = []
    for v in values:
        if v <= 0 or not math.isfinite(v):
            continue
        # Normalise to [1, 10) by dividing by appropriate power of 10
        exp = math.floor(math.log10(v))
        normalised = v / (10 ** exp)
        d = int(normalised)
        if 1 <= d <= 9:
            digits.append(d)
    return digits
