"""Pattern consistency profiler — detects inconsistent string patterns within a column."""
from __future__ import annotations

from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

MINORITY_THRESHOLD = 0.30  # only flag patterns below this threshold
WARNING_THRESHOLD = 0.05  # <5% → WARNING (very rare, likely error); 5-30% → INFO


def _generalize(value: str) -> str:
    """Replace digits with D and letters with L, keeping punctuation as-is.

    Kept for callers that need single-string output (logging, hand-rolled
    callers in tests). Inside the profiler hot path, prefer
    ``_generalize_series`` — vectorising via Polars regex is ~10-20×
    faster on long columns, and `goldencheck.profilers.pattern_consistency._generalize`
    showed up at the top of the cProfile self-time chart for the scale
    audit on 100K rows (3M calls, 12 s self-time; see PR profiling
    Round 4).
    """
    result = []
    for ch in value:
        if ch.isdigit():
            result.append("D")
        elif ch.isalpha():
            result.append("L")
        else:
            result.append(ch)
    return "".join(result)


def _generalize_series(s):
    """Vectorised equivalent of ``_generalize`` for a Polars string Series.

    Pattern: letters (Unicode ``\\p{L}``) → ``L``, then decimal digits
    (``\\d`` = ``\\p{Nd}`` under Polars' Unicode-on regex) → ``D``.

    **Order matters.** Replacing digits first would produce literal ``D``
    characters in the buffer, which the subsequent ``\\p{L}`` pass would
    then re-classify as letters (since ``D`` is itself an ASCII letter).
    Letters-first is safe: ``L`` is not in the digit class, so the digit
    pass leaves the already-replaced letters alone.

    **Documented divergence from per-row ``_generalize``**: Python's
    ``str.isdigit()`` returns True for *compatibility* digit characters
    like ``²``/``³`` (Numeric_Type=Digit) but False for fractions like
    ``½``/``¼`` (Numeric_Type=Numeric). Rust's regex crate exposes only
    Unicode general categories (``\\p{Nd}``, ``\\p{Nl}``, ``\\p{No}``),
    not Numeric_Type, so we can't reproduce that boundary exactly in a
    vectorised pass without a Python callback. We pick the decimal-only
    bucket (``\\p{Nd}``) — matches the dominant case (ASCII 0-9) and
    diverges only on the compat-superscript corner, which doesn't appear
    in production column data this profiler is run against.
    """
    return s.str.replace_all(r"\p{L}", "L").str.replace_all(r"\d", "D")


class PatternConsistencyProfiler(BaseProfiler):
    def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:
        frame = to_frame(frame)
        col = frame.column(column)
        findings: list[Finding] = []

        if col.dtype != "str":
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            return findings

        # Generalise each value to its digit/letter skeleton, then tally the skeletons.
        patterns = non_null.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D")
        pattern_counts = patterns.value_counts_desc()

        n_patterns = len(pattern_counts)
        if n_patterns <= 1:
            # All values share the same pattern — no inconsistency
            return findings

        dominant_pattern, dominant_count = pattern_counts[0]

        # Collect all minority patterns (rarest first — already sorted ascending by reversing)
        minority_candidates = []
        for i in range(1, n_patterns):
            minority_pattern, minority_count = pattern_counts[i]
            minority_count = int(minority_count)
            minority_pct = minority_count / total

            if minority_pct < MINORITY_THRESHOLD:
                minority_candidates.append((minority_pattern, minority_count, minority_pct))

        if not minority_candidates:
            return findings

        # Sort rarest first (most likely errors)
        minority_candidates.sort(key=lambda x: x[1])

        # Cap at top 5
        MAX_PATTERNS = 5
        total_minority = len(minority_candidates)
        emitted = minority_candidates[:MAX_PATTERNS]

        for minority_pattern, minority_count, minority_pct in emitted:
            # <5% → WARNING (very rare, likely error); 5-30% → INFO (valid variant)
            if minority_pct < WARNING_THRESHOLD:
                severity = Severity.WARNING
                confidence = 0.8
            else:
                severity = Severity.INFO
                confidence = 0.5
            # Find sample values that match this minority pattern
            sample_vals = non_null.filter_by(patterns.eq(minority_pattern)).to_list()[:5]

            # Detect structural pattern shift (e.g., letter-first vs digit-first = mixed standards)
            dom_starts_alpha = dominant_pattern and dominant_pattern[0] == "L"
            min_starts_alpha = minority_pattern and minority_pattern[0] == "L"
            if dom_starts_alpha != min_starts_alpha and minority_pct < WARNING_THRESHOLD:
                msg_extra = " — possible invalid format or mixed coding standard"
            else:
                msg_extra = ""

            findings.append(Finding(
                severity=severity,
                column=column,
                check="pattern_consistency",
                message=(
                    f"Inconsistent pattern detected: '{minority_pattern}' appears in "
                    f"{minority_count} row(s) ({minority_pct:.1%}) vs dominant pattern "
                    f"'{dominant_pattern}' ({dominant_count} row(s))" + msg_extra
                ),
                affected_rows=minority_count,
                sample_values=[str(v) for v in sample_vals],
                suggestion="Standardize values to a single format/pattern",
                confidence=confidence,
                metadata={"dominant_pattern": dominant_pattern, "minority_pattern": minority_pattern},
            ))

        # Summary finding if more than MAX_PATTERNS minority patterns exist
        if total_minority > MAX_PATTERNS:
            extra = total_minority - MAX_PATTERNS
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="pattern_consistency",
                message=(
                    f"{extra} additional inconsistent pattern(s) detected (showing top {MAX_PATTERNS})"
                ),
                suggestion="Standardize values to a single format/pattern",
                confidence=0.5,
            ))

        return findings
