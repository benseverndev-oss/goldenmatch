"""Detect a frame that is several sources concatenated together (#2540).

``dedupe_df`` compares every candidate pair inside one frame. When that frame is
actually two catalogues stacked with ``pl.concat``, most of the candidate set is
**wrong by construction** -- pairs drawn from within a single source, which a
cross-source ground truth can never mark correct. Measured on the repo's own
benchmarks: 48.3% of DBLP-ACM's candidate pairs and 73.4% of Abt-Buy's are
within-source. Today that failure is entirely silent.

This module only *detects and reports*. It deliberately does NOT constrain
matching:

* ``match_df`` already owns cross-source linkage. Teaching dedupe a source
  constraint would create a second authoritative owner for one capability,
  against the architecture frame -- so this **routes** to ``match_df`` instead of
  duplicating it.
* Inferring a source and silently dropping pairs would turn a heuristic into a
  correctness dependency. A wrong inference would discard true matches with no
  way for the caller to see it. A warning costs a log line when wrong.

**Why contiguity is the whole signal.** ``pl.concat`` lays source A's rows down
first, then source B's, so a genuine concatenation leaves a *step* in the data:
some column goes null, or changes value-shape, at one row boundary and stays
that way. Real single-source messiness is scattered, not contiguous, which is
what keeps this from being another plausible-but-wrong heuristic. Validated on
the benchmark corpus -- it fires on all three known concatenated benchmarks, at
exactly the true source boundary, and stays silent on all four genuine
single-source corpora:

    dblp_acm       FIRES  row 2616  (DBLP2 is 2616 rows)   format step on `id`
    abt_buy        FIRES  row 1081  (Abt is 1081 rows)     null step on `manufacturer`
    amazon_google  FIRES  row 1363  (Amazon is 1363 rows)  null step on `title`/`name`
    person / household_hardneg / cotenant_hardneg / ncvr_synthetic   silent
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# A step with a few exceptions is still a step: abt_buy's `manufacturer` is null
# for all 1081 Abt rows AND 6 Buy rows, so an exact two-run test misses the real
# concatenation. Tolerance is what makes the signal usable.
_STEP_PURITY = 0.97

# Each side must be a real block. Without this a column that is 3% null anywhere
# would read as a step whose short side is noise.
_MIN_BLOCK_FRAC = 0.10

# Python-level per-value work is O(rows x columns); stride large frames instead.
# A step function stays a step function under uniform striding, so contiguity --
# the entire signal -- survives, unlike head-sampling which would see one source.
_SCAN_ROWS = 20_000


@dataclass(frozen=True)
class ConcatenationEvidence:
    """A detected source boundary and the columns that show it."""

    boundary: int          # approximate row index where the second source starts
    n_rows: int
    columns: tuple[str, ...]   # columns exhibiting the step
    kinds: tuple[str, ...]     # "null" / "format", aligned with `columns`

    def describe(self) -> str:
        parts = [f"{c} ({k} step)" for c, k in zip(self.columns, self.kinds)]
        return ", ".join(parts)


def _best_step(flags: list[bool]) -> tuple[int, float] | None:
    """Boundary that best explains ``flags`` as a step, with its purity.

    Returns ``None`` when either class is too small to be a source block.
    """
    n = len(flags)
    if n < 4:
        return None
    trues = sum(flags)
    floor = _MIN_BLOCK_FRAC * n
    if min(trues, n - trues) < floor:
        return None
    prefix = [0] * (n + 1)
    for i, f in enumerate(flags):
        prefix[i + 1] = prefix[i] + (1 if f else 0)
    lo, hi = int(floor), int(n - floor)
    best_at, best_agree = -1, -1
    for b in range(lo, hi + 1):
        left_true = prefix[b]
        right_true = prefix[n] - prefix[b]
        true_then_false = left_true + ((n - b) - right_true)
        false_then_true = (b - left_true) + right_true
        agree = true_then_false if true_then_false > false_then_true else false_then_true
        if agree > best_agree:
            best_at, best_agree = b, agree
    if best_at < 0:
        return None
    return best_at, best_agree / n


def _signature(value: Any) -> str:
    """Coarse value shape. Distinguishes id families (``conf/vldb/x`` vs ``12345``)
    without pretending to parse them."""
    if value is None:
        return "null"
    s = str(value)
    if not s:
        return "null"
    if s.isdigit():
        return "digits"
    if "/" in s:
        return "path"
    return "alpha"


def detect_concatenated_sources(df: Any) -> ConcatenationEvidence | None:
    """Return evidence that ``df`` is concatenated sources, or ``None``.

    Fail-open: any error returns ``None``. This is advisory, and must never be
    able to break a run it only meant to comment on.
    """
    try:
        from goldenmatch.core.frame import to_frame

        frame = to_frame(df)
        n_full = frame.height
        if n_full < 20:
            return None
        stride = max(1, n_full // _SCAN_ROWS)

        hits_col: list[str] = []
        hits_kind: list[str] = []
        boundaries: list[int] = []

        for col in frame.columns:
            try:
                values = frame.column(col).to_list()
            except Exception:
                continue
            if stride > 1:
                values = values[::stride]
            if len(values) < 20:
                continue

            null_flags = [v is None for v in values]
            got = _best_step(null_flags)
            if got is not None and got[1] >= _STEP_PURITY:
                hits_col.append(col)
                hits_kind.append("null")
                boundaries.append(got[0] * stride)
                continue  # one signal per column is enough

            sigs = [_signature(v) for v in values]
            if len(set(sigs)) < 2:
                continue
            first = sigs[0]
            got = _best_step([s == first for s in sigs])
            if got is not None and got[1] >= _STEP_PURITY:
                hits_col.append(col)
                hits_kind.append("format")
                boundaries.append(got[0] * stride)

        if not hits_col:
            return None
        # Agreeing columns should agree on WHERE the split is; take the median so
        # one odd column cannot move it.
        boundaries.sort()
        boundary = boundaries[len(boundaries) // 2]
        return ConcatenationEvidence(
            boundary=boundary,
            n_rows=n_full,
            columns=tuple(hits_col),
            kinds=tuple(hits_kind),
        )
    except Exception:  # advisory only -- never break the caller
        return None


def warn_if_concatenated_sources(df: Any) -> ConcatenationEvidence | None:
    """Detect and log. Returns the evidence so callers can surface it too."""
    evidence = detect_concatenated_sources(df)
    if evidence is None:
        return None
    pct = 100.0 * evidence.boundary / max(evidence.n_rows, 1)
    logger.warning(
        "This frame looks like %d sources concatenated together: %s change at "
        "row ~%d of %d (%.0f%% / %.0f%%). Dedupe compares records WITHIN this "
        "frame, so pairs drawn from the same source are included -- on a "
        "two-source linkage those cannot be true matches, and they dominated "
        "the candidate set on the benchmarks this was measured against "
        "(48-73%%). If these are distinct sources, use match_df(left, right) "
        "for cross-source linkage. If it is genuinely one population, ignore "
        "this.",
        2, evidence.describe(), evidence.boundary, evidence.n_rows,
        pct, 100.0 - pct,
    )
    return evidence
