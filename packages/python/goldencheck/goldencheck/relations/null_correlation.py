"""Null correlation profiler — detects columns whose null patterns are highly correlated."""
from __future__ import annotations

from itertools import combinations

import polars as pl

from goldencheck.models.finding import Finding, Severity

_DEFAULT_THRESHOLD = 0.90
_HIGH_THRESHOLD = 0.95


class _UnionFind:
    """Simple union-find for grouping correlated columns."""

    def __init__(self, elements: list[str]) -> None:
        self.parent = {e: e for e in elements}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[ry] = rx

    def groups(self) -> list[list[str]]:
        buckets: dict[str, list[str]] = {}
        for e in self.parent:
            root = self.find(e)
            buckets.setdefault(root, []).append(e)
        return list(buckets.values())


class NullCorrelationProfiler:
    """Reports columns whose null/non-null patterns agree >= threshold fraction of rows.

    Confidence scoring:
    - Groups of 3+ columns at >= 95% agreement: confidence=0.8
    - Pairs at >= 95% agreement: confidence=0.5
    - Pairs at 90-95% agreement: confidence=0.4
    """

    def __init__(self, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        findings: list[Finding] = []
        columns = df.columns
        n_rows = len(df)

        if n_rows == 0 or len(columns) < 2:
            return findings

        # Pre-compute null masks (True where null) as Polars Series
        null_masks: dict[str, pl.Series] = {
            col: df[col].is_null() for col in columns
        }
        null_counts: dict[str, int] = {
            col: int(null_masks[col].sum()) for col in columns
        }

        # Find correlated pairs at each threshold tier
        high_pairs: list[tuple[str, str]] = []   # >= _HIGH_THRESHOLD
        low_pairs: list[tuple[str, str]] = []    # >= self.threshold but < _HIGH_THRESHOLD

        for col_a, col_b in combinations(columns, 2):
            null_count_a = null_counts[col_a]
            null_count_b = null_counts[col_b]

            # Skip pairs where neither column has any nulls
            if null_count_a == 0 and null_count_b == 0:
                continue

            # Require >5% nulls in at least one column
            if null_count_a / n_rows <= 0.05 and null_count_b / n_rows <= 0.05:
                continue

            mask_a = null_masks[col_a]
            mask_b = null_masks[col_b]

            # Agreement: rows where both are null or both are non-null
            agreement = int((mask_a == mask_b).sum())
            correlation = agreement / n_rows

            if correlation >= _HIGH_THRESHOLD:
                high_pairs.append((col_a, col_b))
            elif correlation >= self.threshold:
                low_pairs.append((col_a, col_b))

        # Cap: report at most 3 correlation groups total across both tiers
        MAX_GROUPS = 3
        all_group_findings: list[Finding] = []

        # Group high-threshold pairs using union-find
        if high_pairs:
            uf = _UnionFind(columns)
            for col_a, col_b in high_pairs:
                uf.union(col_a, col_b)

            for group in uf.groups():
                if len(group) < 2:
                    continue
                group_sorted = sorted(group)
                group_str = ", ".join(f"'{c}'" for c in group_sorted)
                total_nulls = max(null_counts[c] for c in group)

                if len(group) >= 3:
                    # High confidence — full group finding
                    confidence = 0.8
                else:
                    # Pair at high threshold — medium confidence
                    confidence = 0.5

                all_group_findings.append(
                    Finding(
                        severity=Severity.INFO,
                        column=",".join(group_sorted),
                        check="null_correlation",
                        message=(
                            f"Columns {group_str} have strongly correlated null patterns "
                            f"(>= {_HIGH_THRESHOLD:.0%} agreement). They may represent a logical group."
                        ),
                        affected_rows=total_nulls,
                        suggestion=(
                            "Consider treating these columns as a unit — "
                            "validate that they are all populated or all absent together."
                        ),
                        confidence=confidence,
                    )
                )

        # Report low-threshold pairs (90-95%) with lower confidence
        # Avoid duplicating pairs already covered by the high-threshold grouping
        high_pair_cols: set[frozenset[str]] = {
            frozenset([a, b]) for a, b in high_pairs
        }
        for col_a, col_b in low_pairs:
            if frozenset([col_a, col_b]) in high_pair_cols:
                continue
            pair_sorted = sorted([col_a, col_b])
            pair_str = ", ".join(f"'{c}'" for c in pair_sorted)
            total_nulls = max(null_counts[c] for c in pair_sorted)
            all_group_findings.append(
                Finding(
                    severity=Severity.INFO,
                    column=",".join(pair_sorted),
                    check="null_correlation",
                    message=(
                        f"Columns {pair_str} have moderately correlated null patterns "
                        f"(90-95% agreement). They may represent a logical group."
                    ),
                    affected_rows=total_nulls,
                    suggestion=(
                        "Consider treating these columns as a unit — "
                        "validate that they are all populated or all absent together."
                    ),
                    confidence=0.4,
                )
            )

        # Emit at most MAX_GROUPS findings (prefer higher-confidence ones first)
        all_group_findings.sort(key=lambda f: f.confidence or 0, reverse=True)
        findings.extend(all_group_findings[:MAX_GROUPS])

        return findings
