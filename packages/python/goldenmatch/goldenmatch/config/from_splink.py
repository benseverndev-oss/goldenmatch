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

from goldenmatch.config.schemas import MatchkeyField

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
    kind: Literal["null", "exact", "else", "jaro_winkler", "levenshtein", "jaccard"]
    column: str | None
    sim_threshold: float | None
    approx: bool = False      # True when the mapping is an approximation (jaro->jw, distance->similarity)


def recognize_level(sql: str, *, is_null_level: bool = False) -> RecognizedLevel | None:
    """Recognize a Splink comparison-level `sql_condition` string.

    Returns None when the SQL doesn't match any recognized shape (e.g.
    cross-column comparisons, mismatched columns, or arbitrary SQL) so the
    caller can report a warning and drop the level.
    """
    sql_norm = " ".join(sql.split())

    if is_null_level:
        # Prefer extracting the column from the SQL shape even when the
        # is_null_level flag is what really tells us this is a null level
        # (some Splink serializations put non-null-shaped SQL on the null
        # level, e.g. custom null handling) -- fall back to column=None.
        m = re.fullmatch(_NULL_RE, sql_norm, re.IGNORECASE)
        if m:
            col_l, col_r = m.group(1), m.group(2)
            return RecognizedLevel("null", col_l if col_l == col_r else None, None)
        return RecognizedLevel("null", None, None)

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


def convert_comparison(
    comp: dict, idx: int, report: ConversionReport
) -> MatchkeyField | None:
    """Convert one Splink `comparisons[idx]` dict into a MatchkeyField.

    Returns None (with a warning finding) when the comparison can't be
    represented as a single GoldenMatch scorer family -- e.g. mixed
    comparator families, inconsistent columns, or no usable agree levels.
    """
    comp_path = f"comparisons[{idx}]"
    output_column_name = comp.get("output_column_name") or comp.get("column_name")

    raw_levels = comp.get("comparison_levels", [])

    # (index, RecognizedLevel, raw level dict) for every recognized level.
    recognized: list[tuple[int, RecognizedLevel, dict]] = []
    for j, level in enumerate(raw_levels):
        level_path = f"{comp_path}.comparison_levels[{j}]"
        sql = level.get("sql_condition", "")
        is_null = bool(level.get("is_null_level"))
        r = recognize_level(sql, is_null_level=is_null)
        if r is None:
            report.warn(
                level_path,
                f"unrecognized sql_condition, level dropped: {sql}",
                mapped_to=None,
            )
            continue
        recognized.append((j, r, level))

    null_seen = False
    bands: list[tuple[RecognizedLevel, dict, int]] = []  # agree bands (exact + scorer families)
    for j, r, level in recognized:
        if r.kind == "null":
            null_seen = True
        elif r.kind == "else":
            continue
        else:
            # MatchkeyField thresholds must be in (0, 1]; a converted value
            # outside that range (degenerate levenshtein distance -> sim 0.0,
            # or a nonsense >= 1.5 threshold) can't be represented -- drop the
            # band with a warning rather than let pydantic raise downstream.
            t = r.sim_threshold
            if t is not None and not (0.0 < t <= 1.0):
                report.warn(
                    f"{comp_path}.comparison_levels[{j}]",
                    f"converted threshold {t} out of range (0, 1], level dropped: "
                    f"{level.get('sql_condition', '')}",
                    mapped_to=None,
                )
                continue
            bands.append((r, level, j))

    if null_seen:
        report.info(
            comp_path,
            "Splink null level = no evidence; GoldenMatch scores nulls as "
            "disagree -- behavior differs on sparse fields",
            mapped_to=None,
        )

    families = {r.kind for r, _, _ in bands if r.kind != "exact"}
    if len(families) > 1:
        report.warn(
            comp_path,
            f"mixed comparator families {sorted(families)} in one comparison, "
            "comparison dropped",
            mapped_to=None,
        )
        return None

    if not bands:
        report.warn(comp_path, "no usable agree levels, comparison dropped", mapped_to=None)
        return None

    scorer = next(iter(families)) if families else "exact"

    columns = {r.column for r, _, _ in bands if r.column is not None}
    if len(columns) > 1:
        report.warn(
            comp_path,
            f"inconsistent columns across levels {sorted(columns)}, comparison dropped",
            mapped_to=None,
        )
        return None
    col = next(iter(columns)) if columns else output_column_name
    if col is None:
        report.warn(comp_path, "no column could be determined, comparison dropped", mapped_to=None)
        return None

    for r, level, j in bands:
        if r.approx:
            level_path = f"{comp_path}.comparison_levels[{j}]"
            sql = level.get("sql_condition", "")
            if r.kind == "levenshtein":
                # Reconstruct the original distance from the converted sim.
                distance = round((1 - (r.sim_threshold or 0.0)) * _LEV_ASSUMED_LEN)
                message = (
                    f"approximate mapping: edit distance <= {distance} converted via "
                    f"sim = 1 - distance/{_LEV_ASSUMED_LEN} -> {r.sim_threshold} ({sql})"
                )
            else:
                message = (
                    f"approximate mapping: jaro_similarity treated as jaro_winkler "
                    f"(threshold={r.sim_threshold}) ({sql})"
                )
            report.warn(level_path, message, mapped_to=None)

    thresholds = sorted(
        {r.sim_threshold for r, _, _ in bands if r.sim_threshold is not None}, reverse=True
    )
    levels_count = len(thresholds) + 1

    tf_adjustment = False
    for r, level, j in bands:
        level_path = f"{comp_path}.comparison_levels[{j}]"
        tf_col = level.get("tf_adjustment_column")
        if tf_col:
            if tf_col != col:
                report.warn(
                    level_path,
                    f"tf_adjustment_column '{tf_col}' differs from field column '{col}', "
                    "dropped (GoldenMatch TF adjustment is same-column only)",
                    mapped_to=None,
                )
            else:
                tf_adjustment = True
        tf_weight = level.get("tf_adjustment_weight")
        if tf_weight is not None and tf_weight != 1.0:
            report.warn(
                level_path,
                f"tf_adjustment_weight={tf_weight} dropped (not supported)",
                mapped_to=None,
            )

    mapped_to = f"matchkeys[?].fields[?] ({col})"

    if levels_count == 2:
        if scorer == "exact":
            field = MatchkeyField(field=col, scorer="exact", levels=2, tf_adjustment=tf_adjustment)
        else:
            field = MatchkeyField(
                field=col,
                scorer=scorer,
                levels=2,
                partial_threshold=thresholds[0],
                tf_adjustment=tf_adjustment,
            )
    else:
        field = MatchkeyField(
            field=col,
            scorer=scorer,
            levels=levels_count,
            level_thresholds=thresholds,
            tf_adjustment=tf_adjustment,
        )

    report.info(comp_path, f"converted to field '{col}' (scorer={scorer})", mapped_to=mapped_to)
    return field
