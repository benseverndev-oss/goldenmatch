"""Drift detector — compare a current DataFrame against a saved BaselineProfile."""
from __future__ import annotations

import itertools
import logging
import math
from collections import Counter
from typing import Any

try:
    import numpy as np
    import scipy.stats as _stats
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

import polars as pl

from goldencheck.baseline.models import BaselineProfile
from goldencheck.baseline.patterns import _induce_column_grammars
from goldencheck.baseline.correlation import _cramers_v
from goldencheck.baseline.semantic import infer_semantic_types
from goldencheck.models.finding import Finding, Severity

__all__ = ["run_drift_checks"]

logger = logging.getLogger(__name__)

_SOURCE = "baseline_drift"

# Thresholds
_KS_ERROR_PVALUE = 0.01
_KS_WARN_PVALUE = 0.05
_ENTROPY_DELTA_WARN = 0.5
_BOUND_VIOLATION_RATE = 0.05  # 5%
_FD_VIOLATION_RATE = 0.05
_FD_VIOLATION_MULTIPLIER = 2.0
_TEMPORAL_VIOLATION_RATE = 0.05
_TEMPORAL_VIOLATION_MULTIPLIER = 2.0
_PATTERN_COVERAGE_DROP = 0.05  # 5pp
_PATTERN_NEW_COVERAGE = 0.05
_CORR_STRONG_THRESHOLD = 0.7
_CORR_DROP_THRESHOLD = 0.1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_drift_checks(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """Compare *df* against *baseline* and return all drift findings.

    Parameters
    ----------
    df:
        Current DataFrame to check.
    baseline:
        Previously saved :class:`~goldencheck.baseline.models.BaselineProfile`.

    Returns
    -------
    list[Finding]
        All drift findings, each with ``source="baseline_drift"``.
    """
    findings: list[Finding] = []
    findings.extend(_check_statistical(df, baseline))
    findings.extend(_check_constraints(df, baseline))
    findings.extend(_check_patterns(df, baseline))
    findings.extend(_check_correlations(df, baseline))
    findings.extend(_check_semantic(df, baseline))
    return findings


# ---------------------------------------------------------------------------
# Statistical checks
# ---------------------------------------------------------------------------


def _check_statistical(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    findings: list[Finding] = []

    for col, sp in baseline.stat_profiles.items():
        if col not in df.columns:
            continue

        series = df[col].drop_nulls()
        if len(series) < 30:
            continue

        is_numeric = df[col].dtype in (
            pl.Int8, pl.Int16, pl.Int32, pl.Int64,
            pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
            pl.Float32, pl.Float64,
        )

        if is_numeric:
            findings.extend(_check_distribution_drift(col, series, sp))
            findings.extend(_check_entropy_drift_numeric(col, series, sp))
            findings.extend(_check_bound_violation(col, series, sp))
            findings.extend(_check_benford_drift(col, series, sp))
        else:
            findings.extend(_check_entropy_drift_categorical(col, series, sp))

    return findings


def _check_distribution_drift(col: str, series: pl.Series, sp: Any) -> list[Finding]:
    """KS-test: compare current distribution against the saved fitted distribution."""
    if not _SCIPY_AVAILABLE:
        return []
    if sp.distribution is None or sp.params is None:
        return []

    dist_map = {
        "normal": _stats.norm,
        "log_normal": _stats.lognorm,
        "exponential": _stats.expon,
        "uniform": _stats.uniform,
    }
    dist_obj = dist_map.get(sp.distribution)
    if dist_obj is None:
        return []

    values: np.ndarray = series.cast(pl.Float64).to_numpy()
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return []

    # Reconstruct param tuple from saved dict
    params = sp.params
    try:
        if sp.distribution == "normal":
            fit_params = (params["loc"], params["scale"])
        elif sp.distribution == "log_normal":
            fit_params = (params["s"], params["loc"], params["scale"])
        elif sp.distribution in ("exponential", "uniform"):
            fit_params = (params["loc"], params["scale"])
        else:
            fit_params = tuple(params.values())
    except (KeyError, TypeError):
        return []

    try:
        _stat, pvalue = _stats.kstest(values, dist_obj.name, args=fit_params)
    except Exception as exc:
        logger.debug("KS-test failed for %s: %s", col, exc)
        return []

    if pvalue < _KS_ERROR_PVALUE:
        severity = Severity.ERROR
        msg = (
            f"Distribution drift detected on '{col}': KS-test p={pvalue:.4f} "
            f"(baseline dist={sp.distribution!r}). Data no longer fits baseline distribution."
        )
    elif pvalue < _KS_WARN_PVALUE:
        severity = Severity.WARNING
        msg = (
            f"Possible distribution drift on '{col}': KS-test p={pvalue:.4f} "
            f"(baseline dist={sp.distribution!r})."
        )
    else:
        return []

    return [Finding(
        severity=severity,
        column=col,
        check="distribution_drift",
        message=msg,
        source=_SOURCE,
        confidence=0.9,
        metadata={"technique": "statistical", "drift_type": "distribution_drift",
                  "ks_pvalue": float(pvalue), "baseline_distribution": sp.distribution},
    )]


def _entropy_numeric(values: np.ndarray) -> float:
    """Compute approximate Shannon entropy using a histogram (numeric)."""
    n = len(values)
    if n == 0:
        return 0.0
    n_bins = max(10, min(100, int(math.ceil(math.log2(n) + 1))))
    counts, _ = np.histogram(values, bins=n_bins)
    probs = counts[counts > 0] / n
    return float(-np.sum(probs * np.log2(probs)))


def _entropy(values: list) -> float:
    """Shannon entropy for categorical values."""
    n = len(values)
    if n == 0:
        return 0.0
    counts = Counter(values)
    ent = 0.0
    for cnt in counts.values():
        p = cnt / n
        if p > 0:
            ent -= p * math.log2(p)
    return ent


def _check_entropy_drift_numeric(col: str, series: pl.Series, sp: Any) -> list[Finding]:
    if not _SCIPY_AVAILABLE:
        return []
    values: np.ndarray = series.cast(pl.Float64).to_numpy()
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return []
    current_entropy = _entropy_numeric(values)
    baseline_entropy = sp.entropy
    delta = abs(current_entropy - baseline_entropy)
    if delta <= _ENTROPY_DELTA_WARN:
        return []
    return [Finding(
        severity=Severity.WARNING,
        column=col,
        check="entropy_drift",
        message=(
            f"Entropy drift on '{col}': baseline={baseline_entropy:.3f}, "
            f"current={current_entropy:.3f}, delta={delta:.3f}."
        ),
        source=_SOURCE,
        confidence=0.8,
        metadata={"technique": "statistical", "drift_type": "entropy_drift",
                  "baseline_entropy": baseline_entropy, "current_entropy": current_entropy,
                  "delta": delta},
    )]


def _check_entropy_drift_categorical(col: str, series: pl.Series, sp: Any) -> list[Finding]:
    values = series.cast(pl.Utf8).to_list()
    current_entropy = _entropy(values)
    baseline_entropy = sp.entropy
    delta = abs(current_entropy - baseline_entropy)
    if delta <= _ENTROPY_DELTA_WARN:
        return []
    return [Finding(
        severity=Severity.WARNING,
        column=col,
        check="entropy_drift",
        message=(
            f"Entropy drift on '{col}': baseline={baseline_entropy:.3f}, "
            f"current={current_entropy:.3f}, delta={delta:.3f}."
        ),
        source=_SOURCE,
        confidence=0.8,
        metadata={"technique": "statistical", "drift_type": "entropy_drift",
                  "baseline_entropy": baseline_entropy, "current_entropy": current_entropy,
                  "delta": delta},
    )]


def _check_bound_violation(col: str, series: pl.Series, sp: Any) -> list[Finding]:
    if not _SCIPY_AVAILABLE:
        return []
    bounds = sp.bounds
    p01 = bounds.get("p01")
    p99 = bounds.get("p99")
    if p01 is None or p99 is None:
        return []
    values: np.ndarray = series.cast(pl.Float64).to_numpy()
    values = values[np.isfinite(values)]
    n = len(values)
    if n == 0:
        return []
    violations = int(np.sum((values < p01) | (values > p99)))
    rate = violations / n
    if rate <= _BOUND_VIOLATION_RATE:
        return []
    return [Finding(
        severity=Severity.ERROR,
        column=col,
        check="bound_violation",
        message=(
            f"Bound violation on '{col}': {violations}/{n} values ({rate:.1%}) outside "
            f"baseline p01={p01:.4g} / p99={p99:.4g}."
        ),
        affected_rows=violations,
        source=_SOURCE,
        confidence=0.95,
        metadata={"technique": "statistical", "drift_type": "bound_violation",
                  "violation_rate": rate, "p01": p01, "p99": p99},
    )]


def _check_benford_drift(col: str, series: pl.Series, sp: Any) -> list[Finding]:
    """Warn if Benford's conformance flips (baseline passed, current fails or vice versa)."""
    if not _SCIPY_AVAILABLE:
        return []
    if sp.benford is None:
        return []

    baseline_pvalue = sp.benford.get("chi2_pvalue", 1.0)
    # Baseline conformed to Benford (p >= 0.05 means no significant deviation)
    baseline_conformed = baseline_pvalue >= 0.05

    # Compute current Benford pvalue
    values: np.ndarray = series.cast(pl.Float64).to_numpy()
    values = values[np.isfinite(values) & (values > 0)]
    if len(values) < 30:
        return []

    # Check 2 orders of magnitude
    try:
        span = float(np.log10(np.max(values)) - np.log10(np.min(values)))
    except Exception as exc:
        logger.debug("Benford span computation failed for %s: %s", col, exc)
        return []
    if span < 2.0:
        return []

    current_pvalue = _compute_benford_pvalue(values)
    if current_pvalue is None:
        return []
    current_conformed = current_pvalue >= 0.05

    if baseline_conformed == current_conformed:
        return []

    direction = "no longer conforms" if baseline_conformed else "now conforms (unexpected)"
    return [Finding(
        severity=Severity.WARNING,
        column=col,
        check="benford_drift",
        message=(
            f"Benford's law conformance flip on '{col}': baseline p={baseline_pvalue:.4f}, "
            f"current p={current_pvalue:.4f} — {direction}."
        ),
        source=_SOURCE,
        confidence=0.75,
        metadata={"technique": "statistical", "drift_type": "benford_drift",
                  "baseline_pvalue": baseline_pvalue, "current_pvalue": current_pvalue},
    )]


def _compute_benford_pvalue(values: np.ndarray) -> float | None:
    """Return chi-squared p-value for Benford conformance of values."""
    leading_digits: list[int] = []
    for v in values:
        if v <= 0 or not math.isfinite(v):
            continue
        exp = math.floor(math.log10(v))
        normalised = v / (10 ** exp)
        d = int(normalised)
        if 1 <= d <= 9:
            leading_digits.append(d)
    if not leading_digits:
        return None
    total = len(leading_digits)
    counts = Counter(leading_digits)
    observed = [counts.get(d, 0) for d in range(1, 10)]
    expected = [math.log10(1 + 1 / d) * total for d in range(1, 10)]
    try:
        _chi2, pvalue = _stats.chisquare(f_obs=observed, f_exp=expected)
        return float(pvalue)
    except Exception as exc:
        logger.debug("Benford chi-square test failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Constraint checks
# ---------------------------------------------------------------------------


def _check_constraints(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_check_fd_violations(df, baseline))
    findings.extend(_check_key_uniqueness(df, baseline))
    findings.extend(_check_temporal_order_drift(df, baseline))
    return findings


def _check_fd_violations(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """ERROR if FD violation rate > 2x baseline or > 5%."""
    findings: list[Finding] = []
    n_rows = len(df)
    if n_rows == 0:
        return []

    for fd in baseline.constraints_fd:
        dets = fd.determinant
        deps = fd.dependent

        # All columns must be present
        if not all(c in df.columns for c in dets + deps):
            continue

        # Compute current violation rate (fraction of rows that are NOT consistent
        # with the most common dependent value per determinant group)
        try:
            grouped = (
                df.select(dets + deps)
                .group_by(dets + deps)
                .agg(pl.len().alias("_cnt"))
                .group_by(dets)
                .agg(pl.col("_cnt").max().alias("_mode_cnt"))
            )
            consistent_count = int(grouped["_mode_cnt"].sum())
        except Exception as exc:
            logger.debug("FD violation check failed for %s -> %s: %s", dets, deps, exc)
            continue

        current_confidence = consistent_count / n_rows
        current_violation_rate = 1.0 - current_confidence
        baseline_violation_rate = 1.0 - fd.confidence

        triggered = (
            current_violation_rate > _FD_VIOLATION_RATE
            or (baseline_violation_rate > 0 and
                current_violation_rate > _FD_VIOLATION_MULTIPLIER * baseline_violation_rate)
        )
        if not triggered:
            continue

        affected = n_rows - consistent_count
        det_str = ", ".join(dets)
        dep_str = ", ".join(deps)
        findings.append(Finding(
            severity=Severity.ERROR,
            column=dets[0],
            check="fd_violation",
            message=(
                f"Functional dependency [{det_str}] → [{dep_str}] violated: "
                f"violation rate {current_violation_rate:.1%} "
                f"(baseline {baseline_violation_rate:.1%})."
            ),
            affected_rows=affected,
            source=_SOURCE,
            confidence=0.9,
            metadata={"technique": "constraints", "drift_type": "fd_violation",
                      "determinant": dets, "dependent": deps,
                      "baseline_violation_rate": baseline_violation_rate,
                      "current_violation_rate": current_violation_rate},
        ))
    return findings


def _check_key_uniqueness(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """ERROR if a candidate key gains duplicates."""
    findings: list[Finding] = []
    n_rows = len(df)
    if n_rows == 0:
        return []

    for key_cols in baseline.constraints_keys:
        if not all(c in df.columns for c in key_cols):
            continue

        try:
            sub = df.select(key_cols)
            n_unique = sub.n_unique()
            null_count = sub.null_count().row(0)
            has_nulls = any(nc > 0 for nc in null_count)
        except Exception as exc:
            logger.debug("Key uniqueness check failed for %s: %s", key_cols, exc)
            continue

        if n_unique == n_rows and not has_nulls:
            continue  # Still a valid key

        duplicates = n_rows - n_unique
        col_str = ", ".join(key_cols)
        findings.append(Finding(
            severity=Severity.ERROR,
            column=key_cols[0],
            check="key_uniqueness_loss",
            message=(
                f"Candidate key [{col_str}] has lost uniqueness: "
                f"{duplicates} duplicate(s) found in {n_rows} rows."
            ),
            affected_rows=duplicates,
            source=_SOURCE,
            confidence=0.95,
            metadata={"technique": "constraints", "drift_type": "key_uniqueness_loss",
                      "key_columns": key_cols, "duplicate_count": duplicates},
        ))
    return findings


def _check_temporal_order_drift(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """WARNING if temporal violation rate > 2x baseline or > 5%."""
    findings: list[Finding] = []
    n_rows = len(df)
    if n_rows == 0:
        return []

    for to in baseline.constraints_temporal:
        col_before = to.before
        col_after = to.after

        if col_before not in df.columns or col_after not in df.columns:
            continue

        try:
            a = df[col_before] if df[col_before].dtype in (pl.Date, pl.Datetime) else df[col_before].cast(pl.Date)
            b = df[col_after] if df[col_after].dtype in (pl.Date, pl.Datetime) else df[col_after].cast(pl.Date)
        except Exception as exc:
            logger.debug("Temporal cast failed for (%s, %s): %s", col_before, col_after, exc)
            continue

        tmp = pl.DataFrame({"a": a, "b": b}).drop_nulls()
        if len(tmp) == 0:
            continue

        violations = int((tmp["a"] > tmp["b"]).sum())
        current_violation_rate = violations / len(tmp)
        baseline_violation_rate = to.violation_rate

        triggered = (
            current_violation_rate > _TEMPORAL_VIOLATION_RATE
            or (baseline_violation_rate > 0 and
                current_violation_rate > _TEMPORAL_VIOLATION_MULTIPLIER * baseline_violation_rate)
        )
        if not triggered:
            continue

        findings.append(Finding(
            severity=Severity.WARNING,
            column=col_before,
            check="temporal_order_drift",
            message=(
                f"Temporal order drift: '{col_before}' should be before '{col_after}', "
                f"but violation rate is {current_violation_rate:.1%} "
                f"(baseline {baseline_violation_rate:.1%})."
            ),
            affected_rows=violations,
            source=_SOURCE,
            confidence=0.85,
            metadata={"technique": "constraints", "drift_type": "temporal_order_drift",
                      "before": col_before, "after": col_after,
                      "baseline_violation_rate": baseline_violation_rate,
                      "current_violation_rate": current_violation_rate},
        ))
    return findings


# ---------------------------------------------------------------------------
# Pattern checks
# ---------------------------------------------------------------------------


def _check_patterns(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    findings: list[Finding] = []

    for col, base_grammar in baseline.patterns.items():
        if col not in df.columns:
            continue

        dtype = df[col].dtype
        if dtype not in (pl.Utf8, pl.String):
            continue

        series = df[col].drop_nulls()
        values: list[str] = series.to_list()
        if len(values) < 30:
            continue

        current_grammars = _induce_column_grammars(values)
        current_pattern_map = {g.pattern: g.coverage for g in current_grammars}
        baseline_coverage = base_grammar.coverage
        baseline_pattern = base_grammar.pattern

        # pattern_drift: check if baseline pattern's coverage has dropped
        current_coverage = current_pattern_map.get(baseline_pattern, 0.0)
        drop = baseline_coverage - current_coverage
        if drop > _PATTERN_COVERAGE_DROP:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=col,
                check="pattern_drift",
                message=(
                    f"Pattern coverage drop on '{col}': baseline pattern "
                    f"{baseline_pattern!r} covered {baseline_coverage:.1%}, "
                    f"now {current_coverage:.1%} (drop={drop:.1%})."
                ),
                source=_SOURCE,
                confidence=0.8,
                metadata={"technique": "patterns", "drift_type": "pattern_drift",
                          "pattern": baseline_pattern, "baseline_coverage": baseline_coverage,
                          "current_coverage": current_coverage, "drop": drop},
            ))

        # new_pattern: INFO for new format variants with > 5% coverage not in baseline
        baseline_all_patterns: set[str] = {baseline_pattern}
        for g in current_grammars:
            if g.pattern not in baseline_all_patterns and g.coverage > _PATTERN_NEW_COVERAGE:
                findings.append(Finding(
                    severity=Severity.INFO,
                    column=col,
                    check="new_pattern",
                    message=(
                        f"New format variant on '{col}': pattern {g.pattern!r} "
                        f"covers {g.coverage:.1%} of current data (not in baseline)."
                    ),
                    source=_SOURCE,
                    confidence=0.7,
                    metadata={"technique": "patterns", "drift_type": "new_pattern",
                              "pattern": g.pattern, "coverage": g.coverage},
                ))

    return findings


# ---------------------------------------------------------------------------
# Correlation checks
# ---------------------------------------------------------------------------


def _check_correlations(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    findings: list[Finding] = []

    # Build a lookup of baseline correlations: (col_a, col_b) -> entry
    baseline_lookup: dict[tuple[str, str], Any] = {}
    for entry in baseline.correlations:
        if len(entry.columns) >= 2:
            key = (entry.columns[0], entry.columns[1])
            baseline_lookup[key] = entry

    # Compute current correlations for pairs that exist in baseline
    for key, base_entry in baseline_lookup.items():
        col_a, col_b = key
        if col_a not in df.columns or col_b not in df.columns:
            continue

        current_value = _compute_correlation(df, col_a, col_b, base_entry.measure)
        if current_value is None:
            continue

        # correlation_break: WARNING if strong correlation drops > 0.1
        if base_entry.strength == "strong" and (base_entry.value - current_value) > _CORR_DROP_THRESHOLD:
            drop = base_entry.value - current_value
            findings.append(Finding(
                severity=Severity.WARNING,
                column=col_a,
                check="correlation_break",
                message=(
                    f"Correlation break between '{col_a}' and '{col_b}': "
                    f"baseline={base_entry.value:.3f}, current={current_value:.3f} "
                    f"(drop={drop:.3f}, measure={base_entry.measure})."
                ),
                source=_SOURCE,
                confidence=0.8,
                metadata={"technique": "correlations", "drift_type": "correlation_break",
                          "columns": [col_a, col_b], "measure": base_entry.measure,
                          "baseline_value": base_entry.value, "current_value": current_value,
                          "drop": drop},
            ))

    # new_correlation: INFO for newly emerged strong correlations not in baseline
    # Check numeric-numeric pairs
    numeric_cols = [
        c for c in df.columns
        if df[c].dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                           pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                           pl.Float32, pl.Float64)
        and c in (baseline.columns or df.columns)
    ]

    checked = 0
    for col_a, col_b in itertools.combinations(numeric_cols, 2):
        if checked >= 200:
            break
        key = tuple(sorted([col_a, col_b]))  # type: ignore[assignment]
        if key in baseline_lookup:
            continue
        checked += 1
        # Not in baseline — check if currently strongly correlated
        current_value = _compute_correlation(df, col_a, col_b, "pearson")
        if current_value is not None and abs(current_value) >= _CORR_STRONG_THRESHOLD:
            findings.append(Finding(
                severity=Severity.INFO,
                column=col_a,
                check="new_correlation",
                message=(
                    f"New strong correlation emerged between '{col_a}' and '{col_b}': "
                    f"r={current_value:.3f} (not present in baseline)."
                ),
                source=_SOURCE,
                confidence=0.7,
                metadata={"technique": "correlations", "drift_type": "new_correlation",
                          "columns": [col_a, col_b], "measure": "pearson",
                          "current_value": current_value},
            ))

    return findings


def _compute_correlation(
    df: pl.DataFrame, col_a: str, col_b: str, measure: str
) -> float | None:
    """Compute the requested correlation measure between two columns."""
    if not _SCIPY_AVAILABLE:
        return None

    if measure == "pearson":
        sub = df.select([col_a, col_b]).drop_nulls()
        if len(sub) < 30:
            return None
        try:
            a_vals = sub[col_a].cast(pl.Float64).to_numpy()
            b_vals = sub[col_b].cast(pl.Float64).to_numpy()
            if np.std(a_vals) == 0.0 or np.std(b_vals) == 0.0:
                return None
            corr, _ = _stats.pearsonr(a_vals, b_vals)
            return float(corr) if np.isfinite(corr) else None
        except Exception as exc:
            logger.debug("Pearson correlation failed for (%s, %s): %s", col_a, col_b, exc)
            return None

    if measure == "cramers_v":
        entry = _cramers_v(df, col_a, col_b)
        return entry.value if entry is not None else None

    return None


# ---------------------------------------------------------------------------
# Semantic checks
# ---------------------------------------------------------------------------


def _check_semantic(df: pl.DataFrame, baseline: BaselineProfile) -> list[Finding]:
    """WARNING if a column's semantic type changes from the baseline."""
    if not baseline.semantic_types:
        return []

    findings: list[Finding] = []

    # infer current types — use keyword-only for speed
    current_type_map = infer_semantic_types(df, use_embeddings=False)
    # Invert: col -> type
    current_col_type: dict[str, str] = {}
    for sem_type, cols in current_type_map.items():
        for col in cols:
            current_col_type[col] = sem_type

    for col, baseline_type in baseline.semantic_types.items():
        if col not in df.columns:
            continue
        current_type = current_col_type.get(col)
        if current_type is None or current_type == baseline_type:
            continue
        findings.append(Finding(
            severity=Severity.WARNING,
            column=col,
            check="type_drift",
            message=(
                f"Semantic type drift on '{col}': baseline type was {baseline_type!r}, "
                f"now inferred as {current_type!r}."
            ),
            source=_SOURCE,
            confidence=0.75,
            metadata={"technique": "semantic", "drift_type": "type_drift",
                      "baseline_type": baseline_type, "current_type": current_type},
        ))

    return findings
