"""Approximate functional-dependency VIOLATION detection (cross-column relation).

Where ``FunctionalDependencyProfiler`` reports *strict* dependencies (structure),
this reports *near*-strict ones and surfaces the rows that BREAK them -- the
high-value signal: ``zip -> city`` holds for 99.7% of rows, and the 0.3% that
don't are almost certainly data-entry errors. Reported as WARNING with the
offending rows sampled.

The native kernel (``discover_approximate_fds`` + ``fd_violation_rows``) finds,
per determinant group, the dominant ("mode") dependent value and flags rows that
deviate -- interning each column once and reusing it across pairs. The
pure-Python fallback replicates the identical algorithm (first-seen interning,
mode with first-seen tie-break, same average-group-size guard) so the violation
sets match exactly.

False-positive guard: a near-unique determinant has mostly singleton groups,
each trivially "consistent", which inflates confidence toward 1.0. Both paths
require an average group size >= 3 (``MIN_AVG_GROUP`` in the kernel) so only real
grouping columns qualify.
"""
from __future__ import annotations

import polars as pl

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.models.finding import Finding, Severity

_MIN_ROWS = 100
_MIN_CONFIDENCE = 0.95
_MIN_AVG_GROUP = 3  # must match goldencheck_core::MIN_AVG_GROUP
_MAX_CANDIDATES = 12
_MAX_FINDINGS = 8
_SUPPORTED = (
    pl.Utf8,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Boolean,
)


def _select_candidates(df: pl.DataFrame) -> list[str]:
    scored: list[tuple[int, str]] = []
    for col in df.columns:
        series = df[col]
        if series.dtype not in _SUPPORTED:
            continue
        nu = series.n_unique()
        if nu <= 1:
            continue
        scored.append((nu, col))
    scored.sort(key=lambda t: t[0])  # low-cardinality first (likely determinants)
    return [c for _nu, c in scored[:_MAX_CANDIDATES]]


def _intern(values: list) -> list[int]:
    """First-seen interning matching the native shim: null -> 0, values -> 1,2,…"""
    ids: list[int] = []
    seen: dict = {}
    nxt = 1
    for v in values:
        if v is None:
            ids.append(0)
            continue
        i = seen.get(v)
        if i is None:
            i = nxt
            seen[v] = i
            nxt += 1
        ids.append(i)
    return ids


def _group_modes(det: list[int], dep: list[int]) -> dict[int, int]:
    counts: dict[int, dict[int, int]] = {}
    for d, p in zip(det, dep):
        counts.setdefault(d, {})
        counts[d][p] = counts[d].get(p, 0) + 1
    modes: dict[int, int] = {}
    for d, dep_counts in counts.items():
        best_id, best_cnt = None, -1
        for pid, c in dep_counts.items():
            if c > best_cnt or (c == best_cnt and (best_id is None or pid < best_id)):
                best_cnt, best_id = c, pid
        modes[d] = best_id  # type: ignore[assignment]
    return modes


def _violation_rows(det: list[int], dep: list[int]) -> list[int]:
    modes = _group_modes(det, dep)
    return [r for r, (d, p) in enumerate(zip(det, dep)) if modes.get(d) != p]


def _discover_python(cols_ids: list[list[int]], n_rows: int, min_conf: float) -> list[tuple[int, int, int]]:
    distinct = [len(set(c)) for c in cols_ids]
    out: list[tuple[int, int, int]] = []
    for i in range(len(cols_ids)):
        if distinct[i] == 0 or distinct[i] * _MIN_AVG_GROUP > n_rows:
            continue
        for j in range(len(cols_ids)):
            if i == j or distinct[j] <= 1:
                continue
            viol = len(_violation_rows(cols_ids[i], cols_ids[j]))
            if viol == 0:
                continue
            if 1.0 - viol / n_rows >= min_conf:
                out.append((i, j, viol))
    return out


class ApproximateFDProfiler:
    """Dataset-level relation profiler: near-FD violations (likely errors)."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        n_rows = df.height
        if n_rows < _MIN_ROWS or df.width < 2:
            return []
        cols = _select_candidates(df)
        if len(cols) < 2:
            return []

        triples: list[tuple[int, int, int]]
        violations_of: dict[tuple[int, int], list[int]] = {}
        if native_enabled("approximate_fd"):
            try:
                arrays = [df[c].to_arrow() for c in cols]
                triples = native_module().discover_approximate_fds(arrays, _MIN_CONFIDENCE)
                triples.sort(key=lambda t: t[2])  # fewest violations (highest conf) first
                for i, j, _v in triples[:_MAX_FINDINGS]:
                    violations_of[(i, j)] = native_module().fd_violation_rows(arrays[i], arrays[j])
            except Exception:  # noqa: BLE001 - any native failure -> Python path
                triples, violations_of = self._python(df, cols, n_rows)
        else:
            triples, violations_of = self._python(df, cols, n_rows)

        if not triples:
            return []

        triples.sort(key=lambda t: t[2])  # highest confidence first
        findings: list[Finding] = []
        for i, j, viol in triples[:_MAX_FINDINGS]:
            det, dep = cols[i], cols[j]
            confidence = 1.0 - viol / n_rows
            rows = violations_of.get((i, j), [])[:5]
            samples = [
                f"{det}={df[det][r]!r} has {dep}={df[dep][r]!r}" for r in rows
            ]
            findings.append(Finding(
                severity=Severity.WARNING,
                column=dep,
                check="fd_violation",
                message=(
                    f"'{dep}' is almost always determined by '{det}' "
                    f"({confidence:.1%} of rows); {viol} row(s) break the pattern — "
                    f"likely data-entry errors."
                ),
                affected_rows=viol,
                sample_values=samples,
                suggestion=(
                    f"Review the {viol} row(s) where '{dep}' disagrees with the value "
                    f"'{det}' usually maps to; correct or confirm them."
                ),
                confidence=0.7,
                metadata={
                    "technique": "fd_violation",
                    "determinant": det,
                    "dependent": dep,
                    "fd_confidence": round(confidence, 6),
                    "violation_count": viol,
                },
            ))
        return findings

    def _python(
        self, df: pl.DataFrame, cols: list[str], n_rows: int
    ) -> tuple[list[tuple[int, int, int]], dict[tuple[int, int], list[int]]]:
        cols_ids = [_intern(df[c].to_list()) for c in cols]
        triples = _discover_python(cols_ids, n_rows, _MIN_CONFIDENCE)
        triples.sort(key=lambda t: t[2])
        violations_of = {
            (i, j): _violation_rows(cols_ids[i], cols_ids[j])
            for i, j, _v in triples[:_MAX_FINDINGS]
        }
        return triples, violations_of
