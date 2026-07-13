"""Splink -> GoldenMatch config converter.

Spec: docs/superpowers/specs/2026-07-13-splink-config-converter-design.md
Accepts a Splink settings dict / JSON path (bare or trained model) and
produces a validated GoldenMatchConfig + ConversionReport (+ EMResult when
the input carried trained m/u probabilities).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Literal

Severity = Literal["info", "warning", "error"]

# Splink's levenshtein/damerau_levenshtein comparison levels express a raw edit
# distance threshold (e.g. "<= 1"), while GoldenMatch scorers are normalized
# 0-1 similarities. There is no exact distance->similarity mapping without the
# actual string lengths, so we approximate against an assumed average column
# length: sim = max(0.0, 1 - distance / _LEV_ASSUMED_LEN). This is flagged via
# RecognizedLevel.approx=True so callers can surface it as a lossy conversion.
_LEV_ASSUMED_LEN = 10

# Column atom: Splink serializes comparison-level columns as "col_l" / "col_r"
# (the _l/_r suffix INSIDE the quotes) or bare col_l / col_r.
_COL_L = r'"?([A-Za-z_]\w*)_l"?'
_COL_R = r'"?([A-Za-z_]\w*)_r"?'

_ELSE_RE = r'ELSE'
_NULL_RE = rf'{_COL_L}\s+IS\s+NULL\s+OR\s+{_COL_R}\s+IS\s+NULL'
_EXACT_RE = rf'{_COL_L}\s*=\s*{_COL_R}'
_SIM_RE = (
    r'(jaro_winkler_similarity|jaro_winkler|jaro_similarity|jaccard)'
    rf'\s*\(\s*{_COL_L}\s*,\s*{_COL_R}\s*\)\s*>=\s*([0-9]*\.?[0-9]+)'
)
_DIST_RE = (
    r'(levenshtein|damerau_levenshtein)'
    rf'\s*\(\s*{_COL_L}\s*,\s*{_COL_R}\s*\)\s*<=\s*([0-9]+)'
)

_SIM_KIND = {
    "jaro_winkler_similarity": ("jaro_winkler", False),
    "jaro_winkler": ("jaro_winkler", False),
    "jaro_similarity": ("jaro_winkler", True),
    "jaccard": ("jaccard", False),
}


@dataclass
class RecognizedLevel:
    kind: str                 # "null" | "exact" | "else" | scorer name ("jaro_winkler", "levenshtein", "jaccard")
    column: str | None
    sim_threshold: float | None
    approx: bool = False      # True when the mapping is an approximation (jaro->jw, distance->similarity)


def recognize_level(sql: str, *, is_null_level: bool = False) -> RecognizedLevel | None:
    """Recognize a Splink comparison-level `sql_condition` string.

    Returns None when the SQL doesn't match any recognized shape (e.g.
    cross-column comparisons, mismatched columns, or arbitrary SQL) so the
    caller can report a warning and drop the level.
    """
    if is_null_level:
        return RecognizedLevel("null", None, None)

    sql_norm = " ".join(sql.split())

    if re.fullmatch(_ELSE_RE, sql_norm, re.IGNORECASE):
        return RecognizedLevel("else", None, None)

    m = re.fullmatch(_NULL_RE, sql_norm, re.IGNORECASE)
    if m:
        col_l, col_r = m.group(1), m.group(2)
        return RecognizedLevel("null", col_l, None) if col_l == col_r else None

    m = re.fullmatch(_EXACT_RE, sql_norm, re.IGNORECASE)
    if m:
        col_l, col_r = m.group(1), m.group(2)
        return RecognizedLevel("exact", col_l, 1.0) if col_l == col_r else None

    m = re.fullmatch(_SIM_RE, sql_norm, re.IGNORECASE)
    if m:
        func, col_l, col_r, threshold = m.group(1), m.group(2), m.group(3), float(m.group(4))
        if col_l != col_r:
            return None
        kind, approx = _SIM_KIND[func.lower()]
        return RecognizedLevel(kind, col_l, threshold, approx=approx)

    m = re.fullmatch(_DIST_RE, sql_norm, re.IGNORECASE)
    if m:
        col_l, col_r, distance = m.group(2), m.group(3), int(m.group(4))
        if col_l != col_r:
            return None
        sim = max(0.0, 1 - distance / _LEV_ASSUMED_LEN)
        return RecognizedLevel("levenshtein", col_l, sim, approx=True)

    return None


@dataclass
class ConversionFinding:
    severity: Severity
    splink_path: str      # where in the Splink input, e.g. "comparisons[1].comparison_levels[3]"
    message: str
    mapped_to: str | None  # GoldenMatch destination, e.g. "matchkeys[0].fields[1]"


@dataclass
class ConversionReport:
    findings: list[ConversionFinding] = dc_field(default_factory=list)

    def info(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("info", splink_path, message, mapped_to))

    def warn(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("warning", splink_path, message, mapped_to))

    def error(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("error", splink_path, message, mapped_to))

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def summary(self) -> str:
        counts = {"info": 0, "warning": 0, "error": 0}
        for f in self.findings:
            counts[f.severity] += 1
        return (f"{counts['error']} error(s), {counts['warning']} warning(s), "
                f"{counts['info']} info note(s)")


class SplinkConversionError(ValueError):
    """Raised in strict mode on any lossy mapping, or always on error-severity."""
