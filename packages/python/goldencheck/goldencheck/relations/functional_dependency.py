"""Strict functional-dependency discovery (cross-column relation profiler).

Reports single-column **strict** functional dependencies that hold on the
scanned data: ``det -> dep`` means every value of ``det`` maps to exactly one
value of ``dep``, so ``dep`` is redundant given ``det`` (a lookup / derived
relationship -- e.g. ``zip -> city``, ``dept_id -> dept_name``). This is
distinct from the *approximate* FD mining in ``baseline/constraints.py``
(confidence < 1.0, opt-in via ``create_baseline``); here we surface only exact
dependencies, in the normal scan path, as INFO.

Strict FD `det -> dep` holds iff ``n_distinct(det, dep) == n_distinct(det)``.
When ``goldencheck[native]`` is installed, discovery runs in the Rust kernel
(``discover_functional_dependencies`` -- interns each column once, reuses it
across every pair, and early-exits on the first violation, which beats Polars
recomputing a full two-column distinct per pair). Pure-Polars fallback uses the
``n_unique`` identity above. Both are integer-exact -> identical results.

Guards against noise: needs >= _MIN_ROWS rows of support, skips constant
dependents and unique determinants (trivial), caps candidate columns, and
reports a bounded number of findings.
"""
from __future__ import annotations

import polars as pl

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.models.finding import Finding, Severity

_MIN_ROWS = 50          # enough support that a strict FD isn't a small-sample fluke
_MAX_CANDIDATES = 12    # bound the O(k^2) pair space
_MAX_FINDINGS = 10

_SUPPORTED = (
    pl.Utf8,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Boolean,
)


def _select_candidates(df: pl.DataFrame, n_rows: int) -> list[str]:
    """Supported-dtype, non-constant columns; lowest-cardinality first (the
    interesting determinants), capped."""
    scored: list[tuple[int, str]] = []
    for col in df.columns:
        series = df[col]
        if series.dtype not in _SUPPORTED:
            continue
        nu = series.n_unique()
        if nu <= 1:
            continue
        scored.append((nu, col))
    scored.sort(key=lambda t: t[0])
    return [c for _nu, c in scored[:_MAX_CANDIDATES]]


def _discover_polars(df: pl.DataFrame, cols: list[str], n_rows: int) -> list[tuple[int, int]]:
    """Pure-Polars mirror of the kernel: det->dep holds iff
    n_distinct(det, dep) == n_distinct(det). Skips trivial pairs identically."""
    distinct = {c: df[c].n_unique() for c in cols}
    out: list[tuple[int, int]] = []
    for i, det in enumerate(cols):
        if distinct[det] == n_rows:  # unique determinant -> trivial
            continue
        for j, dep in enumerate(cols):
            if i == j or distinct[dep] <= 1:
                continue
            if df.select([det, dep]).n_unique() == distinct[det]:
                out.append((i, j))
    return out


class FunctionalDependencyProfiler:
    """Dataset-level relation profiler: discover strict single-column FDs."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        n_rows = df.height
        if n_rows < _MIN_ROWS or df.width < 2:
            return []

        cols = _select_candidates(df, n_rows)
        if len(cols) < 2:
            return []

        pairs: list[tuple[int, int]]
        if native_enabled("functional_dependencies"):
            try:
                arrays = [df[c].to_arrow() for c in cols]
                pairs = native_module().discover_functional_dependencies(arrays)
            except Exception:  # noqa: BLE001 - any native failure -> Polars path
                pairs = _discover_polars(df, cols, n_rows)
        else:
            pairs = _discover_polars(df, cols, n_rows)

        if not pairs:
            return []

        # Merge by determinant (det -> {deps}) so A->B and A->C are one finding.
        det_to_deps: dict[str, list[str]] = {}
        for i, j in pairs:
            det_to_deps.setdefault(cols[i], []).append(cols[j])

        findings: list[Finding] = []
        for det in sorted(det_to_deps, key=lambda d: (len(det_to_deps[d]), d), reverse=True):
            deps = sorted(det_to_deps[det])
            deps_str = ", ".join(deps)
            findings.append(Finding(
                severity=Severity.INFO,
                column=det,
                check="functional_dependency",
                message=(
                    f"Column '{det}' determines ({deps_str}) — each '{det}' value maps "
                    f"to a single value of {'these columns' if len(deps) > 1 else 'this column'}, "
                    f"so {'they are' if len(deps) > 1 else 'it is'} redundant given '{det}'."
                ),
                affected_rows=n_rows,
                sample_values=[],
                suggestion=(
                    "If this is a lookup relationship, consider normalizing "
                    f"({deps_str} into a table keyed by '{det}') to remove redundancy."
                ),
                confidence=0.55,
                metadata={"technique": "functional_dependency", "determinant": det, "dependents": deps},
            ))
            if len(findings) >= _MAX_FINDINGS:
                break
        return findings
