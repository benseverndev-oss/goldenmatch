"""Constraint mining: functional dependencies, candidate keys, temporal orders."""
from __future__ import annotations

import logging

from goldencheck._polars_lazy import pl
from goldencheck.baseline.models import FunctionalDependency, TemporalOrder
from goldencheck.core.kernels import (
    discover_approximate_fds,
    discover_functional_dependencies,
)

__all__ = ["mine_constraints"]

logger = logging.getLogger(__name__)

# Maximum number of columns to consider (prevents O(n^2) blowup on wide tables).
_MAX_COLS = 30
# Maximum unique values a column may have to be treated as "low-cardinality".
_MAX_UNIQUE = 1000
# Minimum rows required before mining anything.
_MIN_ROWS = 30


def mine_constraints(
    df: pl.DataFrame,
    min_confidence: float = 0.95,
    date_columns: list[str] | None = None,
) -> tuple[list[FunctionalDependency], list[list[str]], list[TemporalOrder]]:
    """Mine structural constraints from *df*.

    Returns
    -------
    fds:
        Functional dependencies with confidence >= *min_confidence*.
        FDs sharing the same determinant are merged so that A->B and A->C
        become a single ``FunctionalDependency(determinant=[A], dependent=[B, C])``.
    candidate_keys:
        Single-column candidate keys — columns that are 100 % unique and
        contain no null values.  Each key is returned as ``[column_name]``.
    temporal_orders:
        Pairwise ordering constraints for the columns listed in
        *date_columns*.  Only pairs whose violation rate is below 0.5 are
        returned (reversed if the "natural" direction has a majority of
        violations).
    """
    if len(df) < _MIN_ROWS:
        return [], [], []

    fds = _mine_functional_dependencies(df, min_confidence)
    keys = _mine_candidate_keys(df)
    temporal = _mine_temporal_orders(df, date_columns or [])
    return fds, keys, temporal


# ---------------------------------------------------------------------------
# Functional dependency mining (simplified TANE — single-column determinants)
# ---------------------------------------------------------------------------


def _mine_functional_dependencies(
    df: pl.DataFrame,
    min_confidence: float,
) -> list[FunctionalDependency]:
    """Return merged FDs with confidence >= *min_confidence*.

    Runs the native-gated FD kernels over the candidate columns instead of a
    per-pair Polars ``group_by``: the strict pass (``discover_functional_dependencies``,
    confidence 1.0) and the approximate pass (``discover_approximate_fds``,
    confidence in ``[min_confidence, 1.0)`` = ``1 - violations/n_rows``) together
    cover the ``>= min_confidence`` range. The Rust kernel interns each column
    once and reuses it across every pair (early-exit on the first violation for
    the strict pass); the no-native fallback does its distinct-/mode-counting in
    pure Python. Either way the FD-mining compute leaves the Polars engine (the
    baseline module's Polars-eviction step) — only candidate selection still
    inspects the frame. The strict pass also drops the trivial FDs a
    ``group_by`` counted as confidence 1.0 (a unique determinant "determines"
    every column), and the approximate pass applies the shared average-group-size
    guard, so near-unique determinants no longer surface spurious dependencies.
    """
    # Select the 30 lowest-cardinality columns to avoid O(k^2) blowup.
    cardinalities: list[tuple[str, int]] = []
    for col in df.columns:
        n_unique = df[col].n_unique()
        if n_unique < _MAX_UNIQUE:
            cardinalities.append((col, n_unique))

    # Sort ascending by cardinality and take up to _MAX_COLS.
    cardinalities.sort(key=lambda x: x[1])
    candidate_cols = [col for col, _ in cardinalities[:_MAX_COLS]]
    if len(candidate_cols) < 2:
        return []

    n_rows = len(df)
    # Pull each candidate column out once (extraction, not compute) so the
    # kernels run on plain Python lists — no Polars in the FD-mining path.
    values = [df[c].to_list() for c in candidate_cols]

    # Accumulate: determinant -> {dependent -> max_confidence}. The strict and
    # approximate passes are disjoint by construction (0 vs >=1 violations).
    det_to_deps: dict[str, dict[str, float]] = {}
    for i, j in discover_functional_dependencies(values):
        det_to_deps.setdefault(candidate_cols[i], {})[candidate_cols[j]] = 1.0
    for i, j, viol in discover_approximate_fds(values, min_confidence):
        confidence = 1.0 - viol / n_rows
        dep_map = det_to_deps.setdefault(candidate_cols[i], {})
        if confidence > dep_map.get(candidate_cols[j], 0.0):
            dep_map[candidate_cols[j]] = confidence

    # Merge: for each determinant, combine all dependent columns into one FD.
    # Use the minimum confidence across dependents (most conservative).
    fds: list[FunctionalDependency] = []
    for det, dep_map in det_to_deps.items():
        if not dep_map:
            continue
        dependents = sorted(dep_map.keys())
        confidence = min(dep_map.values())
        fds.append(
            FunctionalDependency(
                determinant=[det],
                dependent=dependents,
                confidence=confidence,
            )
        )

    return fds


# ---------------------------------------------------------------------------
# Candidate key detection
# ---------------------------------------------------------------------------


def _mine_candidate_keys(df: pl.DataFrame) -> list[list[str]]:
    """Return single-column candidate keys (100 % unique, no nulls)."""
    n_rows = len(df)
    keys: list[list[str]] = []
    for col in df.columns:
        series = df[col]
        if series.null_count() == 0 and series.n_unique() == n_rows:
            keys.append([col])
    return keys


# ---------------------------------------------------------------------------
# Temporal order mining
# ---------------------------------------------------------------------------


def _mine_temporal_orders(
    df: pl.DataFrame,
    date_columns: list[str],
) -> list[TemporalOrder]:
    """Check all pairs of *date_columns* for consistent ordering."""
    present = [c for c in date_columns if c in df.columns]
    if len(present) < 2:
        return []

    orders: list[TemporalOrder] = []

    for i, col_a in enumerate(present):
        for col_b in present[i + 1 :]:
            try:
                # TODO(W-path): route via dtype_category
                a = df[col_a] if df[col_a].dtype in (pl.Date, pl.Datetime) else df[col_a].cast(pl.Date)
                b = df[col_b] if df[col_b].dtype in (pl.Date, pl.Datetime) else df[col_b].cast(pl.Date)
            except Exception as exc:
                logger.debug("Date cast failed for (%s, %s): %s", col_a, col_b, exc)
                continue

            # Drop rows where either value is null.
            tmp = pl.DataFrame({"a": a, "b": b}).drop_nulls()
            if len(tmp) == 0:
                continue

            # Violations of a < b  (i.e. rows where a > b).
            violations_ab = (tmp["a"] > tmp["b"]).sum()
            violation_rate = violations_ab / len(tmp)

            if violation_rate < 0.5:
                # col_a is naturally before col_b.
                orders.append(
                    TemporalOrder(
                        before=col_a,
                        after=col_b,
                        violation_rate=float(violation_rate),
                    )
                )
            else:
                # Majority suggests col_b before col_a — report reversed.
                reversed_violation_rate = 1.0 - violation_rate
                orders.append(
                    TemporalOrder(
                        before=col_b,
                        after=col_a,
                        violation_rate=float(reversed_violation_rate),
                    )
                )

    return orders
