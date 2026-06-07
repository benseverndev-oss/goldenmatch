"""Referential-integrity checks across TWO files (foreign-key validation).

GoldenCheck's scan path is single-file; this fills the common cross-table gap:
do a child table's foreign-key values all exist in the parent's key? E.g.
``orders.customer_id`` must be a subset of ``customers.id``. Reports orphan rows
(FK values with no parent match), the orphan rate, and the join cardinality.

Pure-Polars (``is_in`` over the parent key set + a few aggregations) -- set
membership is already a fast vectorized Polars path, so no native kernel.

Public API:
- ``check_referential_integrity(child_df, parent_df, mappings, ...)`` -> findings
- ``referential_integrity_files(child_path, parent_path, on=None)`` -> findings
- ``auto_detect_mappings(child_df, parent_df)`` -> [(child_col, parent_col)]
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from goldencheck.engine.reader import read_file
from goldencheck.models.finding import Finding, Severity

# Orphan rate above this => ERROR; any orphans below it => WARNING.
_ERROR_ORPHAN_RATE = 0.01
_MAX_SAMPLE = 5


def _is_key(series: pl.Series) -> bool:
    """A column usable as a parent key: non-null and fully unique."""
    n = series.len()
    return n > 0 and series.null_count() == 0 and series.n_unique() == n


def auto_detect_mappings(
    child_df: pl.DataFrame, parent_df: pl.DataFrame
) -> list[tuple[str, str]]:
    """Same-named columns that are a unique+non-null key on the parent side."""
    mappings: list[tuple[str, str]] = []
    for col in child_df.columns:
        if col in parent_df.columns and _is_key(parent_df[col]):
            mappings.append((col, col))
    return mappings


def check_referential_integrity(
    child_df: pl.DataFrame,
    parent_df: pl.DataFrame,
    mappings: list[tuple[str, str]],
    *,
    child_name: str = "child",
    parent_name: str = "parent",
) -> list[Finding]:
    """For each (child_fk, parent_key) mapping, find orphan FK values."""
    findings: list[Finding] = []
    for child_col, parent_col in mappings:
        if child_col not in child_df.columns or parent_col not in parent_df.columns:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=child_col,
                check="referential_integrity",
                message=(
                    f"Cannot check '{child_name}.{child_col}' -> "
                    f"'{parent_name}.{parent_col}': column not found."
                ),
                confidence=0.9,
                metadata={"technique": "referential_integrity"},
            ))
            continue

        child_fk = child_df[child_col]
        non_null = child_fk.drop_nulls()
        considered = non_null.len()
        if considered == 0:
            continue

        parent_keys = parent_df[parent_col].drop_nulls().unique()
        orphan_mask = ~non_null.is_in(parent_keys)
        orphan_rows = int(orphan_mask.sum())

        # Join cardinality (informational): is each side unique on the key?
        child_unique = child_fk.n_unique() == child_fk.len()
        parent_unique = _is_key(parent_df[parent_col])
        cardinality = (
            ("1" if child_unique else "N") + ":" + ("1" if parent_unique else "N")
        )

        if orphan_rows == 0:
            findings.append(Finding(
                severity=Severity.INFO,
                column=child_col,
                check="referential_integrity",
                message=(
                    f"'{child_name}.{child_col}' fully references "
                    f"'{parent_name}.{parent_col}' ({considered} rows, {cardinality})."
                ),
                affected_rows=0,
                confidence=0.9,
                metadata={
                    "technique": "referential_integrity",
                    "child_column": child_col, "parent_column": parent_col,
                    "cardinality": cardinality, "orphan_rows": 0,
                },
            ))
            continue

        orphan_values = non_null.filter(orphan_mask)
        distinct_orphans = orphan_values.n_unique()
        rate = orphan_rows / considered
        severity = Severity.ERROR if rate > _ERROR_ORPHAN_RATE else Severity.WARNING
        samples = [str(v) for v in orphan_values.unique().head(_MAX_SAMPLE).to_list()]
        findings.append(Finding(
            severity=severity,
            column=child_col,
            check="referential_integrity",
            message=(
                f"{orphan_rows} row(s) ({distinct_orphans} distinct value(s), {rate:.1%}) in "
                f"'{child_name}.{child_col}' have no match in '{parent_name}.{parent_col}' "
                f"— orphaned foreign keys."
            ),
            affected_rows=orphan_rows,
            sample_values=samples,
            suggestion=(
                f"Backfill the missing '{parent_name}.{parent_col}' rows, or treat these "
                f"orphaned '{child_col}' values as invalid."
            ),
            confidence=0.85,
            metadata={
                "technique": "referential_integrity",
                "child_column": child_col, "parent_column": parent_col,
                "cardinality": cardinality, "orphan_rows": orphan_rows,
                "distinct_orphans": distinct_orphans, "orphan_rate": round(rate, 6),
            },
        ))
    return findings


def _parse_on(spec: list[str]) -> list[tuple[str, str]]:
    """Parse ``--on`` specs: 'child=parent' or 'col' (same name both sides)."""
    out: list[tuple[str, str]] = []
    for s in spec:
        if "=" in s:
            c, p = s.split("=", 1)
            out.append((c.strip(), p.strip()))
        else:
            out.append((s.strip(), s.strip()))
    return out


def referential_integrity_files(
    child_path: Path,
    parent_path: Path,
    on: list[str] | None = None,
) -> list[Finding]:
    """Read both files and check referential integrity. When ``on`` is omitted,
    auto-detect FK mappings from same-named parent-key columns."""
    child_df = read_file(Path(child_path))
    parent_df = read_file(Path(parent_path))
    mappings = _parse_on(on) if on else auto_detect_mappings(child_df, parent_df)
    if not mappings:
        return [Finding(
            severity=Severity.INFO,
            column="__dataset__",
            check="referential_integrity",
            message=(
                "No foreign-key relationship detected (no shared unique-key column). "
                "Pass --on child_col=parent_col to check explicitly."
            ),
            confidence=0.5,
            metadata={"technique": "referential_integrity"},
        )]
    return check_referential_integrity(
        child_df, parent_df, mappings,
        child_name=Path(child_path).name, parent_name=Path(parent_path).name,
    )
