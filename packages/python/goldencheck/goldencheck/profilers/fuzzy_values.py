"""Fuzzy near-duplicate VALUE detection (column profiler).

Flags columns whose distinct values include edit-distance-close variants -- the
classic inconsistent-categorical smell: a ``state`` column holding
``"California"``, ``"Californa"``, ``"CALIFORNIA"``; a ``name`` column with
``"Jon"`` and ``"John"``. This complements:
  - the cardinality / enum profiler (which sees N distinct values but not that
    several are the *same* thing mis-typed), and
  - ApproxDuplicateProfiler (which catches values equal *after* normalization;
    here the values still differ after normalization but are typo-close).

Whole-ROW fuzzy matching is intentionally NOT done here -- that's entity
resolution (GoldenMatch). This is a bounded, per-column value check: it runs on
a column's *distinct* values (a small set for the categorical columns it
targets), with trigram + prefix blocking and a Levenshtein-ratio scorer.

Native (``goldencheck[native]``) runs the blocking + pairwise edit distance in
Rust -- pairwise Levenshtein is the part that is slow in Python, so this kernel
genuinely beats the fallback. The pure-Python fallback (used when the ext is
absent) computes the same clusters with the same algorithm via difflib.
"""
from __future__ import annotations

import polars as pl

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

# Only target genuinely "categorical" string columns: enough rows to trust, and
# a distinct-value count in a range where near-dups are meaningful (not a free-
# text column with thousands of unique values, not a near-constant flag).
_MIN_ROWS = 50
_MIN_DISTINCT = 3
_MAX_DISTINCT = 2000
_MIN_SIMILARITY = 0.82
_MAX_CLUSTERS_REPORTED = 5


def _python_clusters(values: list[str], min_similarity: float) -> list[list[int]]:
    """Pure-Python fallback mirroring goldencheck_core::near_duplicate_clusters.

    Same normalization, trigram + 2-char-prefix blocking, Levenshtein-ratio
    threshold, and union-find clustering -- so results match the native kernel."""
    import re

    def normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower()).strip()

    norm = [normalize(v) for v in values]
    n = len(values)
    trigram: dict[str, list[int]] = {}
    prefix: dict[str, list[int]] = {}
    for i, s in enumerate(norm):
        if len(s) < 3:
            continue
        for k in range(len(s) - 2):
            trigram.setdefault(s[k:k + 3], []).append(i)
        prefix.setdefault(s[:2], []).append(i)

    candidates: set[tuple[int, int]] = set()
    for bucket in list(trigram.values()) + list(prefix.values()):
        if len(bucket) < 2 or len(bucket) > 300:
            continue
        for a in range(len(bucket)):
            for b in range(a + 1, len(bucket)):
                i, j = bucket[a], bucket[b]
                candidates.add((i, j) if i < j else (j, i))

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def levenshtein(a: str, b: str) -> int:
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            cur = [i + 1]
            for j, cb in enumerate(b):
                cost = 0 if ca == cb else 1
                cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + cost))
            prev = cur
        return prev[len(b)]

    def lev_ratio(a: str, b: str) -> float:
        # Identical metric to goldencheck_core::similarity, so the fallback
        # clusters match the native kernel exactly.
        maxlen = max(len(a), len(b))
        if maxlen == 0:
            return 1.0
        return 1.0 - levenshtein(a, b) / maxlen

    linked = False
    for i, j in candidates:
        if lev_ratio(norm[i], norm[j]) >= min_similarity:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj
            linked = True
    if not linked:
        return []

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    clusters.sort()
    return clusters


class FuzzyValuesProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        if df.height < _MIN_ROWS:
            return []
        col = df[column]
        if col.dtype != pl.Utf8:
            return []
        distinct = col.drop_nulls().unique()
        n_distinct = distinct.len()
        if n_distinct < _MIN_DISTINCT or n_distinct > _MAX_DISTINCT:
            return []

        values: list[str] = distinct.to_list()

        clusters: list[list[int]]
        if native_enabled("fuzzy_values"):
            try:
                clusters = native_module().near_duplicate_value_clusters(values, _MIN_SIMILARITY)
            except Exception:  # noqa: BLE001 - any native failure -> Python path
                clusters = _python_clusters(values, _MIN_SIMILARITY)
        else:
            clusters = _python_clusters(values, _MIN_SIMILARITY)

        if not clusters:
            return []

        # Largest clusters first; report a bounded number.
        clusters.sort(key=len, reverse=True)
        findings: list[Finding] = []
        for cluster in clusters[:_MAX_CLUSTERS_REPORTED]:
            variants = [values[i] for i in cluster]
            shown = variants[:6]
            findings.append(Finding(
                severity=Severity.WARNING,
                column=column,
                check="fuzzy_duplicate_values",
                message=(
                    f"Column '{column}' has {len(variants)} near-duplicate values that look "
                    f"like variants of one another: {', '.join(repr(v) for v in shown)}"
                    f"{' …' if len(variants) > len(shown) else ''}."
                ),
                affected_rows=int(col.is_in(variants).sum()),
                sample_values=[str(v) for v in shown],
                suggestion=(
                    "Standardize these to a single canonical value (casing/spelling), "
                    "or define an enum, so they reconcile."
                ),
                confidence=0.6,
                metadata={"technique": "fuzzy_duplicate_values", "variants": variants},
            ))
        return findings
