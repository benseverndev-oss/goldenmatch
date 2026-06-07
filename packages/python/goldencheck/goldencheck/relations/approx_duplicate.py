"""Approximate / exact duplicate-row detection (cross-column relation profiler).

GoldenCheck's uniqueness profiler works per-column; nothing flags whole ROWS
that are duplicated or near-duplicated. This profiler covers both:

- **Exact duplicate rows** -- byte-identical records (often a join fan-out or a
  double-load bug).
- **Near-duplicate rows** -- records that become identical after a light
  normalization of their string fields (lowercase, collapse whitespace, drop
  punctuation): ``"Acme, Inc."`` vs ``"acme inc"`` vs ``"ACME  Inc"``. These are
  the most common real-world dupes that exact matching misses.

Intentionally pure-Polars: the work is a normalize + group-by, which Polars
already does vectorized + multithreaded. Per the native-kernel lesson
(packages/python/goldencheck/CLAUDE.md), we don't reach for Rust where Polars is
already the fast path -- the gate is "beat Polars", and here it wouldn't.

True edit-distance fuzzy matching (typos that survive normalization, e.g.
``Jon``/``John``) needs blocking + pairwise scoring and is a heavier follow-up;
this profiler is the deterministic normalize-and-group tier.
"""
from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity

_SEP = "\x1f"  # unit separator -- won't appear in normal data
_MAX_SAMPLE = 5


def _normalized_signature(df: pl.DataFrame) -> pl.Series:
    """One signature string per row: string columns normalized (lowercased,
    punctuation/whitespace collapsed), other columns cast as-is. Two rows share
    a signature iff they are equal after that normalization."""
    exprs = []
    for name, dtype in zip(df.columns, df.dtypes):
        col = pl.col(name).cast(pl.Utf8).fill_null("")
        if dtype == pl.Utf8:
            # lowercase -> collapse any run of non-alphanumerics to a single
            # space -> trim. "Acme, Inc." and "acme  inc" both -> "acme inc".
            col = col.str.to_lowercase().str.replace_all(r"[^0-9a-z]+", " ").str.strip_chars()
        exprs.append(col.alias(name))
    return df.select(pl.concat_str(exprs, separator=_SEP)).to_series()


def _exact_signature(df: pl.DataFrame) -> pl.Series:
    """Raw row signature -- byte-identical rows share it."""
    exprs = [pl.col(name).cast(pl.Utf8).fill_null("") for name in df.columns]
    return df.select(pl.concat_str(exprs, separator=_SEP)).to_series()


class ApproxDuplicateProfiler:
    """Dataset-level relation profiler: exact + near-duplicate rows."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        n_rows = df.height
        if n_rows < 2 or df.width == 0:
            return []

        work = pl.DataFrame({
            "__norm__": _normalized_signature(df),
            "__exact__": _exact_signature(df),
        })
        norm_counts = work.group_by("__norm__").len().rename({"len": "__nc__"})
        exact_counts = work.group_by("__exact__").len().rename({"len": "__ec__"})
        work = work.join(norm_counts, on="__norm__").join(exact_counts, on="__exact__")

        findings: list[Finding] = []

        # Exact duplicate rows (any row whose exact signature repeats).
        exact_dups = work.filter(pl.col("__ec__") >= 2)
        if exact_dups.height > 0:
            n_groups = exact_dups["__exact__"].n_unique()
            findings.append(Finding(
                severity=Severity.WARNING,
                column="__dataset__",
                check="duplicate_rows",
                message=(
                    f"{exact_dups.height} rows are exact duplicates "
                    f"({n_groups} distinct duplicated record{'s' if n_groups != 1 else ''})."
                ),
                affected_rows=exact_dups.height,
                sample_values=[],
                suggestion=(
                    "De-duplicate before downstream processing, or confirm the "
                    "repetition is intentional (e.g. a denormalized fact table)."
                ),
                confidence=0.7,
                metadata={"technique": "duplicate_rows", "duplicate_groups": n_groups},
            ))

        # Near-duplicates: share a normalized signature with another row but have
        # NO exact twin -- i.e. they differ only by case / whitespace / punctuation.
        near_dups = work.filter((pl.col("__nc__") >= 2) & (pl.col("__ec__") < 2))
        if near_dups.height > 0:
            n_groups = near_dups["__norm__"].n_unique()
            findings.append(Finding(
                severity=Severity.WARNING,
                column="__dataset__",
                check="near_duplicate_rows",
                message=(
                    f"{near_dups.height} rows are near-duplicates — identical after "
                    f"lowercasing, collapsing whitespace, and removing punctuation "
                    f"({n_groups} group{'s' if n_groups != 1 else ''})."
                ),
                affected_rows=near_dups.height,
                sample_values=[],
                suggestion=(
                    "Standardize casing/whitespace/punctuation (or run an entity-"
                    "resolution pass) so these records reconcile to one."
                ),
                confidence=0.6,
                metadata={"technique": "near_duplicate_rows", "near_duplicate_groups": n_groups},
            ))

        return findings
