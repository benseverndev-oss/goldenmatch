"""Pattern grammar inducer — derive regex grammars from string columns."""
from __future__ import annotations

import re
from collections import Counter

import polars as pl

from goldencheck.baseline.models import PatternGrammar

__all__ = ["induce_patterns", "_induce_column_grammars"]

# Minimum number of rows required to run pattern induction.
_MIN_ROWS = 30

# Minimum fractional coverage for a grammar to be reported.
_MIN_COVERAGE = 0.03

# Regex special characters that must be escaped when used as literals in patterns.
_REGEX_SPECIAL = frozenset(r"\.^$*+?{}[]|()")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_skeleton(value: str) -> str:
    """Convert a string value to its character-class skeleton.

    Uppercase letters → 'A', lowercase letters → 'a', digits → '0',
    all other characters are kept as-is.

    Examples:
        "ABC-1234" → "AAA-0000"
        "Hello World" → "Aaaaa Aaaaa"
    """
    result: list[str] = []
    for ch in value:
        if ch.isupper():
            result.append("A")
        elif ch.islower():
            result.append("a")
        elif ch.isdigit():
            result.append("0")
        else:
            result.append(ch)
    return "".join(result)


def _escape_literal(ch: str) -> str:
    """Return a regex-safe version of a literal (non-class) character."""
    if ch in _REGEX_SPECIAL:
        return re.escape(ch)
    return ch


def _skeleton_to_regex(skeleton: str) -> str:
    """Convert a character-class skeleton to a compact regex pattern.

    Consecutive identical skeleton characters are merged into {N} quantifiers.

    Examples:
        "AAA-0000" → "[A-Z]{3}-[0-9]{4}"
        "aa0"      → "[a-z]{2}[0-9]{1}"
    """
    if not skeleton:
        return ""

    # Group consecutive identical characters
    groups: list[tuple[str, int]] = []
    current = skeleton[0]
    count = 1
    for ch in skeleton[1:]:
        if ch == current:
            count += 1
        else:
            groups.append((current, count))
            current = ch
            count = 1
    groups.append((current, count))

    parts: list[str] = []
    for ch, n in groups:
        if ch == "A":
            parts.append(f"[A-Z]{{{n}}}")
        elif ch == "a":
            parts.append(f"[a-z]{{{n}}}")
        elif ch == "0":
            parts.append(f"[0-9]{{{n}}}")
        else:
            # Literal character — escape if needed
            escaped = _escape_literal(ch)
            if n == 1:
                parts.append(escaped)
            else:
                parts.append(f"{escaped}{{{n}}}")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _induce_column_grammars(values: list[str]) -> list[PatternGrammar]:
    """Induce pattern grammars from a list of string values.

    Returns a list of PatternGrammar objects for patterns whose coverage
    meets or exceeds _MIN_COVERAGE (3%). Patterns are sorted descending
    by coverage. Identical patterns are merged (coverage summed).

    Args:
        values: Non-null string values from a single column.

    Returns:
        List of PatternGrammar objects, sorted by coverage descending.
    """
    if not values:
        return []

    total = len(values)
    skeleton_counts: Counter[str] = Counter()
    skeleton_to_regex: dict[str, str] = {}

    for val in values:
        skel = _to_skeleton(val)
        skeleton_counts[skel] += 1
        if skel not in skeleton_to_regex:
            skeleton_to_regex[skel] = _skeleton_to_regex(skel)

    # Build grammars, merging identical regex patterns
    pattern_coverage: dict[str, float] = {}
    for skel, cnt in skeleton_counts.items():
        coverage = cnt / total
        if coverage < _MIN_COVERAGE:
            continue
        regex = skeleton_to_regex[skel]
        pattern_coverage[regex] = pattern_coverage.get(regex, 0.0) + coverage

    # Sort descending by coverage
    grammars = [
        PatternGrammar(pattern=pat, coverage=round(cov, 6))
        for pat, cov in sorted(pattern_coverage.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return grammars


def induce_patterns(df: pl.DataFrame) -> dict[str, list[PatternGrammar]]:
    """Induce pattern grammars for all string columns in a DataFrame.

    Only processes columns with dtype pl.Utf8 (String). Numeric and other
    types are skipped. Requires at least 30 rows in the DataFrame.

    Args:
        df: Input Polars DataFrame.

    Returns:
        A mapping of column name → list of PatternGrammar objects.
        Columns with no grammar meeting the 3% threshold are omitted.
    """
    if df.height < _MIN_ROWS:
        return {}

    result: dict[str, list[PatternGrammar]] = {}

    for col_name in df.columns:
        dtype = df[col_name].dtype
        # Only process string / Utf8 columns
        if dtype != pl.Utf8 and dtype != pl.String:
            continue

        # Drop nulls and extract Python strings
        series = df[col_name].drop_nulls()
        values: list[str] = series.to_list()

        if len(values) < _MIN_ROWS:
            continue

        grammars = _induce_column_grammars(values)
        if grammars:
            result[col_name] = grammars

    return result
