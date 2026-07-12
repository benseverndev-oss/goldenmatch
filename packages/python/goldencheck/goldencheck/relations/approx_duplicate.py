"""Approximate / exact duplicate-row detection (cross-column relation profiler).

GoldenCheck's uniqueness profiler works per-column; nothing flags whole ROWS
that are duplicated or near-duplicated. This profiler covers both:

- **Exact duplicate rows** -- byte-identical records (often a join fan-out or a
  double-load bug).
- **Near-duplicate rows** -- records that become identical after a light
  normalization of their string fields (lowercase, collapse whitespace, drop
  punctuation): ``"Acme, Inc."`` vs ``"acme inc"`` vs ``"ACME  Inc"``. These are
  the most common real-world dupes that exact matching misses.

**Flip (Stage A4): kernel-authoritative.** The four counts (exact rows/groups,
near rows/groups) come from the fused native ``duplicate_signatures`` kernel over
the Arrow columns -- byte/set-parity-validated in W3 (tests/engine/test_w3_shadow.py).
When the native kernel is unavailable, a polars-free pure-Python fallback
reproduces the identical normalize-and-group signatures. Nothing on this path
requires Polars.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from goldencheck._polars_lazy import pl
from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity

logger = logging.getLogger(__name__)

_SEP = "\x1f"  # unit separator -- won't appear in normal data
_NORM_RE = re.compile(r"[^0-9a-z]+")


# --- Polars parity-reference helpers -----------------------------------------
# The profiler is now kernel-authoritative (Arrow + duplicate_signatures). These
# two Polars helpers are retained ONLY as the byte-for-byte ground-truth oracle
# for the W3 parity test (tests/engine/test_w3_shadow.py). They are lazy (no
# Polars import at module load) and are NOT on the scan path.
def _normalized_signature(df: pl.DataFrame) -> pl.Series:
    """One signature string per row: string columns normalized (lowercased,
    punctuation/whitespace collapsed), other columns cast as-is."""
    exprs = []
    for name, dtype in zip(df.columns, df.dtypes):
        col = pl.col(name).cast(pl.Utf8).fill_null("")
        if dtype == pl.Utf8:
            col = col.str.to_lowercase().str.replace_all(r"[^0-9a-z]+", " ").str.strip_chars()
        exprs.append(col.alias(name))
    return df.select(pl.concat_str(exprs, separator=_SEP)).to_series()


def _exact_signature(df: pl.DataFrame) -> pl.Series:
    """Raw row signature -- byte-identical rows share it."""
    exprs = [pl.col(name).cast(pl.Utf8).fill_null("") for name in df.columns]
    return df.select(pl.concat_str(exprs, separator=_SEP)).to_series()


def _cast_str(value) -> str:
    """``pl.col(x).cast(pl.Utf8).fill_null("")`` equivalent for a Python scalar:
    null -> "", bool -> lowercase, everything else -> ``str(v)``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _py_duplicate_signatures(col_lists, is_string) -> tuple[int, int, int, int]:
    """Polars-free mirror of ``goldencheck_core::duplicate_signatures``: build a
    per-row exact + normalized signature, then count rows/groups. Matches the
    kernel's four counts on identical input."""
    n = len(col_lists[0]) if col_lists else 0
    exact_sigs: list[str] = []
    norm_sigs: list[str] = []
    for r in range(n):
        exact_parts: list[str] = []
        norm_parts: list[str] = []
        for ci, vals in enumerate(col_lists):
            s = _cast_str(vals[r])
            exact_parts.append(s)
            if is_string[ci]:
                norm_parts.append(_NORM_RE.sub(" ", s.lower()).strip())
            else:
                norm_parts.append(s)
        exact_sigs.append(_SEP.join(exact_parts))
        norm_sigs.append(_SEP.join(norm_parts))

    ec = Counter(exact_sigs)
    nc = Counter(norm_sigs)
    exact_rows = sum(1 for s in exact_sigs if ec[s] >= 2)
    exact_groups = sum(1 for c in ec.values() if c >= 2)
    near_rows = 0
    near_group_sigs: set[str] = set()
    for i in range(n):
        if nc[norm_sigs[i]] >= 2 and ec[exact_sigs[i]] < 2:
            near_rows += 1
            near_group_sigs.add(norm_sigs[i])
    return exact_rows, exact_groups, near_rows, len(near_group_sigs)


class ApproxDuplicateProfiler:
    """Dataset-level relation profiler: exact + near-duplicate rows."""

    def profile(self, frame) -> list[Finding]:
        frame = to_frame(frame)
        cols = frame.columns
        n_rows = frame.height
        if n_rows < 2 or len(cols) == 0:
            return []

        seam_cols = [frame.column(c) for c in cols]
        # is_string selects the columns normalized for the near-dup signature. It
        # mirrors the prior ``dt == pl.Utf8`` mask: a Polars Categorical maps to
        # neutral "other" (NOT "str"), so it is excluded exactly as before.
        is_string = [c.dtype == "str" for c in seam_cols]

        edr = edg = ndr = ndg = 0
        if native_enabled("duplicate_signatures"):
            try:
                arrays = [c.to_arrow() for c in seam_cols]
                edr, edg, ndr, ndg = native_module().duplicate_signatures(arrays, is_string)
            except Exception as e:  # noqa: BLE001 - native failure -> pure-Python path
                logger.debug("duplicate_signatures kernel failed, using Python fallback: %s", e)
                edr, edg, ndr, ndg = _py_duplicate_signatures(
                    [c.to_list() for c in seam_cols], is_string
                )
        else:
            edr, edg, ndr, ndg = _py_duplicate_signatures(
                [c.to_list() for c in seam_cols], is_string
            )

        findings: list[Finding] = []

        # Exact duplicate rows (any row whose exact signature repeats).
        if edr > 0:
            findings.append(Finding(
                severity=Severity.WARNING,
                column="__dataset__",
                check="duplicate_rows",
                message=(
                    f"{edr} rows are exact duplicates "
                    f"({edg} distinct duplicated record{'s' if edg != 1 else ''})."
                ),
                affected_rows=edr,
                sample_values=[],
                suggestion=(
                    "De-duplicate before downstream processing, or confirm the "
                    "repetition is intentional (e.g. a denormalized fact table)."
                ),
                confidence=0.7,
                metadata={"technique": "duplicate_rows", "duplicate_groups": edg},
            ))

        # Near-duplicates: share a normalized signature with another row but have
        # NO exact twin -- i.e. they differ only by case / whitespace / punctuation.
        if ndr > 0:
            findings.append(Finding(
                severity=Severity.WARNING,
                column="__dataset__",
                check="near_duplicate_rows",
                message=(
                    f"{ndr} rows are near-duplicates — identical after "
                    f"lowercasing, collapsing whitespace, and removing punctuation "
                    f"({ndg} group{'s' if ndg != 1 else ''})."
                ),
                affected_rows=ndr,
                sample_values=[],
                suggestion=(
                    "Standardize casing/whitespace/punctuation (or run an entity-"
                    "resolution pass) so these records reconcile to one."
                ),
                confidence=0.6,
                metadata={"technique": "near_duplicate_rows", "near_duplicate_groups": ndg},
            ))

        return findings
