"""Splink -> GoldenMatch config converter.

Spec: docs/superpowers/specs/2026-07-13-splink-config-converter-design.md
Accepts a Splink settings dict / JSON path (bare or trained model) and
produces a validated GoldenMatchConfig + ConversionReport (+ EMResult when
the input carried trained m/u probabilities).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Literal

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core._paths import safe_path
from goldenmatch.core.probabilistic import EMResult

Severity = Literal["info", "warning", "error"]

# Splink's levenshtein/damerau_levenshtein comparison levels express a raw edit
# distance threshold (e.g. "<= 1"), while GoldenMatch scorers are normalized
# 0-1 similarities. There is no exact distance->similarity mapping without the
# actual string lengths, so we approximate against an assumed average column
# length: sim = max(0.0, 1 - distance / _LEV_ASSUMED_LEN). This is flagged via
# RecognizedLevel.approx=True so callers can surface it as a lossy conversion.
_LEV_ASSUMED_LEN = 10

# convert_comparison emits its per-comparison success finding with this
# mapped_to placeholder; from_splink()'s _patch_field_placeholders resolves it
# to the field's final position once the matchkey is assembled. One shared
# constant so producer and consumer can't drift.
_PLACEHOLDER_PREFIX = "matchkeys[?].fields[?]"

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

LevelKind = Literal["null", "exact", "else", "jaro_winkler", "levenshtein", "jaccard"]

_SIM_KIND: dict[str, tuple[LevelKind, bool]] = {
    "jaro_winkler_similarity": ("jaro_winkler", False),
    "jaro_winkler": ("jaro_winkler", False),
    "jaro_similarity": ("jaro_winkler", True),
    "jaccard": ("jaccard", False),
}


@dataclass
class RecognizedLevel:
    kind: LevelKind
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

    mapped_to = f"{_PLACEHOLDER_PREFIX} ({col})"

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


# ── Blocking rules -> BlockingConfig ─────────────────────────────────────────
#
# Splink blocking rules use the l."col" / r."col" PREFIX style (unlike
# comparison levels, which use the col_l / col_r SUFFIX style handled above).
_BLOCK_COL_L = r'l\."?(\w+)"?'
_BLOCK_COL_R = r'r\."?(\w+)"?'
_BLOCK_EXACT_RE = re.compile(rf'{_BLOCK_COL_L}\s*=\s*{_BLOCK_COL_R}', re.IGNORECASE)
# SUBSTR(col, start, len) is SQL's 1-based, inclusive-length form. The repo's
# `substring:<start>:<end>` transform (goldenmatch/utils/transforms.py:35-39)
# is a Python slice: `value[start:end]`. So SUBSTR(x, 1, 4) (chars 1-4) maps
# to substring:0:4 (python_start = sql_start - 1, python_end = python_start +
# sql_len). Verified by reading transforms.py directly, not assumed.
_BLOCK_SUBSTR_RE = re.compile(
    rf'SUBSTR(?:ING)?\s*\(\s*{_BLOCK_COL_L}\s*,\s*(\d+)\s*,\s*(\d+)\s*\)'
    rf'\s*=\s*SUBSTR(?:ING)?\s*\(\s*{_BLOCK_COL_R}\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',
    re.IGNORECASE,
)
# IS NOT NULL guard conjunct (#1783): Splink CustomRule blocking rules
# routinely carry `AND l.col IS NOT NULL` guards on the equality columns. GM's
# blocker already implements those semantics (a null component nulls the whole
# concat_str key and null keys form no block -- core/blocker.py), so guards on
# key columns are recognized here and ignored exactly, instead of dropping the
# whole rule as unparseable. `[lr]` covers both prefixes under IGNORECASE;
# fixed words with `\s+` separators keep the pattern linear (no nested
# quantifiers -- py/polynomial-redos convention as the ' AND ' split above).
_BLOCK_NOT_NULL_RE = re.compile(r'[lr]\."?(\w+)"?\s+IS\s+NOT\s+NULL', re.IGNORECASE)


@dataclass
class _BlockConjunct:
    field: str
    transform: str | None  # e.g. "substring:0:4", or None for plain equality
    is_null_guard: bool = False  # True for `l.col IS NOT NULL` guard conjuncts


def _strip_outer_parens(s: str) -> str:
    """Strip balanced outer parentheses: '(l.a = r.a)' -> 'l.a = r.a'.

    Splink 4's ``block_on(...)`` serialization wraps every AND-conjunct in
    parentheses (e.g. ``(l."surname" = r."surname") AND (SUBSTRING(l.dob, 1,
    4) = SUBSTRING(r.dob, 1, 4))``), so after splitting on AND each conjunct
    arrives paren-wrapped. Only strips when the opening paren's match is the
    final character (so ``SUBSTR(a) = SUBSTR(b)`` is untouched).
    """
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        closes_at_end = False
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = i == len(s) - 1
                    break
        if not closes_at_end:
            break
        s = s[1:-1].strip()
    return s


def _recognize_blocking_conjunct(conjunct: str) -> _BlockConjunct | None:
    """Recognize one top-level AND-conjunct of a Splink blocking_rule.

    Returns None for anything not a same-column equality, a same-column/
    same-offset SUBSTR(...) equality, or an ``IS NOT NULL`` guard (OR,
    cross-column, arithmetic, ranges, etc. all fail to fullmatch and are
    rejected here). Balanced outer parentheses (Splink 4 ``block_on``
    serialization style) are stripped before matching.
    """
    conjunct = _strip_outer_parens(conjunct)

    m = _BLOCK_SUBSTR_RE.fullmatch(conjunct)
    if m:
        col_l, start_l, len_l, col_r, start_r, len_r = m.groups()
        if col_l != col_r or start_l != start_r or len_l != len_r:
            return None
        sql_start, sql_len = int(start_l), int(len_l)
        # Degenerate args: SQL SUBSTR is 1-based, so start < 1 has no clean
        # Python-slice equivalent (py_start=-1 would wrap); length 0 would
        # produce an empty key (one mega-block). Reject as unrecognized.
        if sql_start < 1 or sql_len < 1:
            return None
        py_start = sql_start - 1
        py_end = py_start + sql_len
        return _BlockConjunct(col_l, f"substring:{py_start}:{py_end}")

    m = _BLOCK_EXACT_RE.fullmatch(conjunct)
    if m:
        col_l, col_r = m.groups()
        if col_l != col_r:
            return None
        return _BlockConjunct(col_l, None)

    m = _BLOCK_NOT_NULL_RE.fullmatch(conjunct)
    if m:
        return _BlockConjunct(m.group(1), None, is_null_guard=True)

    return None


def _convert_one_blocking_rule(
    rule: dict | str, idx: int, report: ConversionReport
) -> BlockingKeyConfig | None:
    rule_path = f"blocking_rules[{idx}]"
    sql = rule.get("blocking_rule", rule) if isinstance(rule, dict) else rule
    if not isinstance(sql, str):
        report.warn(
            rule_path,
            f"blocking rule is not a SQL string, dropped: {rule!r}",
            mapped_to=None,
        )
        return None
    sql_norm = " ".join(sql.split())

    # Whole-rule strip handles the double-wrapped case; each conjunct is
    # deliberately stripped AGAIN inside _recognize_blocking_conjunct, so
    # neither call can be "simplified" away.
    # sql_norm collapsed all whitespace runs to single spaces above, so a
    # literal ' AND ' split is exact -- and unlike r'\s+AND\s+' it cannot
    # backtrack polynomially on adversarial whitespace (py/polynomial-redos).
    conjuncts = re.split(r' AND ', _strip_outer_parens(sql_norm), flags=re.IGNORECASE)
    recognized: list[_BlockConjunct] = []
    for conjunct in conjuncts:
        r = _recognize_blocking_conjunct(conjunct)
        if r is None:
            report.warn(
                rule_path,
                f"unrecognized blocking rule, dropped: {sql_norm}",
                mapped_to=None,
            )
            return None
        recognized.append(r)

    # Partition IS NOT NULL guards from key components (#1783). A rule with
    # ONLY guards has nothing to block on -> the unrecognized-drop path.
    guards = [r for r in recognized if r.is_null_guard]
    recognized = [r for r in recognized if not r.is_null_guard]
    if not recognized:
        report.warn(
            rule_path,
            f"unrecognized blocking rule, dropped: {sql_norm}",
            mapped_to=None,
        )
        return None

    # Dedupe repeated fields (order-preserving): `l.a = r.a AND l.a = r.a`
    # is one field, not two identical key components.
    fields = list(dict.fromkeys(r.field for r in recognized))
    # BlockingKeyConfig.transforms is ONE chain applied uniformly to every
    # field in the key (core/blocker.py:_build_block_key_expr) -- there is no
    # per-field transform slot. A mixed rule (plain equality on one column +
    # SUBSTR on another) is therefore approximated as a single key carrying
    # the substring transform for ALL fields. This is safe for blocking
    # (candidate generation only needs to be a superset of true matches;
    # applying substring to the plain-equality field just widens that block
    # slightly -- no recall loss, just looser precision at the blocking
    # stage). If two conjuncts specify SUBSTR at different offsets, there is
    # no single chain that represents both, so the rule is dropped.
    transform_values = {r.transform for r in recognized if r.transform is not None}
    if len(transform_values) > 1:
        report.warn(
            rule_path,
            f"conflicting SUBSTR offsets across fields, rule dropped: {sql_norm}",
            mapped_to=None,
        )
        return None
    transforms = [next(iter(transform_values))] if transform_values else []

    # IS NOT NULL guards (#1783): GM's blocker already nulls the whole key
    # when any key field is null (concat_str default) and filters null keys
    # before blocks form, so a guard on a KEY column adds zero information --
    # ignored exactly (info, so strict=True is not tripped on a faithful
    # conversion). A guard on a NON-key column can't be expressed (GM has no
    # null-constraint slot outside the key): dropping it makes the candidates
    # a superset of Splink's -- safe for blocking (scoring decides), but lossy
    # -> warn, matching the SUBSTR-widening convention below.
    guard_fields = list(dict.fromkeys(g.field for g in guards))
    ignored_guards = [g for g in guard_fields if g in fields]
    extra_guards = [g for g in guard_fields if g not in fields]
    if ignored_guards:
        report.info(
            rule_path,
            f"null guards ignored (implicit in GM blocking): {ignored_guards}",
            mapped_to=None,
        )
    if extra_guards:
        report.warn(
            rule_path,
            f"approximate mapping: IS NOT NULL guard(s) on non-key column(s) "
            f"{extra_guards} dropped (GoldenMatch cannot express a "
            "null-constraint on a column outside the blocking key); "
            f"candidates are a superset of Splink's ({sql_norm})",
            mapped_to=None,
        )

    key = BlockingKeyConfig(fields=fields, transforms=transforms)
    plain_fields = list(dict.fromkeys(r.field for r in recognized if r.transform is None))
    if transforms and plain_fields:
        # LOSSY: the key-level chain applies the substring transform to
        # field(s) Splink compared with plain equality. Warn (not info) so
        # strict=True gates on it, matching the approx-warn convention used
        # for comparison levels above.
        report.warn(
            rule_path,
            f"approximate mapping, blocking key widened: {transforms[0]} "
            f"applied to all fields including plain-equality field(s) "
            f"{plain_fields} (GoldenMatch transforms are key-level); "
            "candidates are a superset of Splink's, precision may drop "
            "(superset guarantee assumes skip_oversized stays False, the "
            f"converter's emitted default) ({sql_norm})",
            mapped_to=None,
        )
    else:
        report.info(
            rule_path,
            f"converted to blocking key fields={fields} transforms={transforms}",
            mapped_to=None,
        )
    return key


def convert_blocking(rules: list, report: ConversionReport) -> BlockingConfig | None:
    """Convert Splink `blocking_rules_to_generate_predictions` into a
    GoldenMatch `BlockingConfig`.

    Each rule is a string (or a Splink 4 dict `{"blocking_rule": ..., ...}`).
    One surviving rule -> `strategy="static"`; two or more -> `strategy=
    "multi_pass"` with both `keys` and `passes` set to the same list (the
    convention `core/autoconfig_rules.py:_with_multi_pass` uses). If every
    rule is dropped, this is fatal: GoldenMatch probabilistic matchkeys
    require a blocking config, so an error finding is recorded and None is
    returned rather than an invalid config.
    """
    keys: list[BlockingKeyConfig] = []
    for idx, rule in enumerate(rules):
        key = _convert_one_blocking_rule(rule, idx, report)
        if key is not None:
            keys.append(key)

    if not keys:
        report.error(
            "blocking_rules",
            "no blocking rule could be converted to a BlockingConfig key",
            mapped_to=None,
        )
        return None

    if len(keys) == 1:
        return BlockingConfig(strategy="static", keys=keys)
    return BlockingConfig(strategy="multi_pass", keys=keys, passes=keys)


# ── Trained-model (m/u) import ───────────────────────────────────────────────
#
# Splink lists comparison levels strongest -> weakest (after the null level),
# and each non-null level MAY carry an EM-trained "m_probability" /
# "u_probability". GoldenMatch's `EMResult` is the mirror image: level index 0
# is disagree (Splink's ELSE), index N-1 is the strongest agree level. So
# importing m/u is a re-indexing exercise, not a re-fit.


def detect_trained(settings: dict) -> bool:
    """True if any comparison level anywhere in ``settings`` carries m/u.

    A bare (untrained) Splink settings dict has comparison levels with only
    ``sql_condition`` (+ metadata); a trained model additionally carries
    ``m_probability`` / ``u_probability`` floats on every non-null level.
    Checking for the presence of the key anywhere is enough to distinguish
    the two shapes without assuming every level was populated.
    """
    for comp in settings.get("comparisons", []):
        for level in comp.get("comparison_levels", []):
            if "m_probability" in level or "u_probability" in level:
                return True
    return False


def _agree_index_for(r: RecognizedLevel, fld: MatchkeyField) -> int | None:
    """Resolve a recognized agree-band level to its GoldenMatch level index.

    Resolution is by position in the field's own (already-deduped, descending)
    thresholds -- mirrors ``convert_comparison``'s threshold derivation.
    Returns ``None`` when the level's threshold matches none of the field's
    converted thresholds (its m/u mass is dropped by the caller).
    """
    if fld.levels == 2:
        if fld.scorer == "exact":
            return 1 if r.kind == "exact" else None
        if r.sim_threshold is None or fld.partial_threshold is None:
            return None
        return 1 if abs(r.sim_threshold - fld.partial_threshold) < 1e-9 else None
    if r.sim_threshold is None:
        return None
    for i, t in enumerate(fld.level_thresholds or []):
        if abs(t - r.sim_threshold) < 1e-9:
            return (fld.levels - 1) - i
    return None


def import_em(
    comparisons: list[tuple[dict, int, MatchkeyField]],
    settings: dict,
    report: ConversionReport,
) -> EMResult | None:
    """Import trained m/u probabilities into an :class:`EMResult`.

    ``comparisons`` is the explicit alignment the caller (the top-level
    ``convert()``, Task 11) must build: one ``(comp_dict, comp_idx, field)``
    tuple per Splink comparison that ``convert_comparison`` successfully
    turned into a ``MatchkeyField``. Re-deriving that alignment here would
    duplicate `convert_comparison`'s dropping/merging logic; taking it as an
    explicit parameter keeps this function a pure "does this comparison carry
    m/u, and if so where does it go" walk over ``comp["comparison_levels"]``,
    reusing `recognize_level` (imported above) instead of re-implementing SQL
    recognition.

    Returns ``None`` when no level in any comparison carries m/u at all (a
    bare, untrained settings dict) or when nothing importable survives.
    """
    if not detect_trained(settings):
        return None

    m_probs: dict[str, list[float]] = {}
    u_probs: dict[str, list[float]] = {}
    epsilon = 1e-6

    for comp, comp_idx, fld in comparisons:
        # convert_comparison always sets `field` on the MatchkeyFields it
        # emits, so the Optional is impossible here; narrow it once for the
        # dict keys below.
        field_name = fld.field
        assert field_name is not None
        n = fld.levels
        m_acc = [0.0] * n
        u_acc = [0.0] * n
        assigned = [False] * n
        lost_m = 0.0
        lost_u = 0.0
        had_any_prob = False
        comp_path = f"comparisons[{comp_idx}]"

        for j, level in enumerate(comp.get("comparison_levels", [])):
            level_path = f"{comp_path}.comparison_levels[{j}]"
            m_p = level.get("m_probability")
            u_p = level.get("u_probability")
            if m_p is None and u_p is None:
                continue
            had_any_prob = True

            # Partial data: a level carrying only one side (m without u, or
            # u without m). Silently treating the missing side as 0.0 would
            # skew log2(m/u) hard; floor it with epsilon and warn instead.
            if m_p is None or u_p is None:
                missing_side = "m_probability" if m_p is None else "u_probability"
                mapped_prefix = "em.m_probs" if missing_side == "m_probability" else "em.u_probs"
                report.warn(
                    level_path,
                    f"level carries partial trained data ({missing_side} "
                    f"missing) for field '{fld.field}'; missing side filled "
                    f"with epsilon ({epsilon})",
                    mapped_to=f"{mapped_prefix}.{fld.field}",
                )
                if m_p is None:
                    m_p = epsilon
                else:
                    u_p = epsilon

            is_null = bool(level.get("is_null_level"))
            sql = level.get("sql_condition", "")
            r = recognize_level(sql, is_null_level=is_null)

            if r is None:
                # Unrecognized level (already dropped by convert_comparison
                # when building the field) -- its m/u mass is lost.
                lost_m += m_p or 0.0
                lost_u += u_p or 0.0
                report.warn(
                    level_path,
                    "unrecognized level carried m/u probabilities; dropped, "
                    f"surviving levels for field '{fld.field}' re-normalized",
                    mapped_to=f"em.m_probs.{fld.field}",
                )
                continue

            if r.kind == "null":
                # Splink convention: null levels carry no evidentiary m/u.
                # Ignore even if present rather than let them participate.
                continue

            if r.kind == "else":
                idx = 0
            else:
                idx = _agree_index_for(r, fld)
                if idx is None:
                    lost_m += m_p or 0.0
                    lost_u += u_p or 0.0
                    report.warn(
                        level_path,
                        f"level threshold {r.sim_threshold} does not match any "
                        f"converted threshold for field '{fld.field}'; m/u "
                        "dropped, surviving levels re-normalized",
                        mapped_to=f"em.m_probs.{fld.field}",
                    )
                    continue

            # Two Splink levels can collapse onto the same GoldenMatch index
            # (Task 8's threshold dedupe) -- sum their m/u rather than
            # overwrite, and warn: the collapse is lossy (two Splink levels
            # become one GoldenMatch level).
            if assigned[idx]:
                report.warn(
                    level_path,
                    f"level collapsed onto GoldenMatch level {idx} of field "
                    f"'{fld.field}' (duplicate threshold after dedupe); m/u "
                    "probabilities summed with the earlier level's",
                    mapped_to=f"em.m_probs.{fld.field}",
                )
            m_acc[idx] += m_p or 0.0
            u_acc[idx] += u_p or 0.0
            assigned[idx] = True

        if not had_any_prob:
            # This comparison carried no trained data at all (mixed
            # bare/trained input); nothing to import for this field. The
            # resulting model is PARTIAL: it cannot be used via model_path
            # (FS model validation requires coverage of every matchkey
            # field), so surface it loudly rather than skipping silently.
            report.warn(
                comp_path,
                f"comparison for field '{fld.field}' carries no trained m/u "
                "while other comparisons do (mixed bare/trained input); the "
                f"imported model will NOT cover field '{fld.field}', and "
                "using it via model_path with this partial model will fail "
                "validation at runtime",
                mapped_to=None,
            )
            continue

        if lost_m or lost_u:
            report.warn(
                comp_path,
                f"re-normalizing m/u probabilities for field '{fld.field}' "
                "after dropping unrecognized level(s)",
                mapped_to=f"em.m_probs.{fld.field}",
            )

        for i in range(n):
            if not assigned[i]:
                m_acc[i] = epsilon
                u_acc[i] = epsilon
                report.warn(
                    comp_path,
                    f"field '{fld.field}' level {i} had no m/u probability "
                    "assigned from the Splink model; filled with epsilon",
                    mapped_to=f"em.m_probs.{fld.field}",
                )

        sum_m = sum(m_acc)
        sum_u = sum(u_acc)
        m_final = [v / sum_m for v in m_acc] if sum_m > 0 else [1.0 / n] * n
        u_final = [v / sum_u for v in u_acc] if sum_u > 0 else [1.0 / n] * n

        m_probs[field_name] = m_final
        u_probs[field_name] = u_final

    if not m_probs:
        return None

    # Splink model exports carry no term-frequency tables, so an imported
    # EMResult always has tf_freqs=None -- tf_adjustment on a converted field
    # silently no-ops until the model is retrained. Say so.
    tf_fields = [
        fld.field for _, _, fld in comparisons
        if fld.tf_adjustment and fld.field is not None
    ]
    if tf_fields:
        report.info(
            "comparisons",
            "term-frequency tables are not part of a Splink model export; "
            f"tf_adjustment on field(s) {', '.join(sorted(set(tf_fields)))} "
            "will only take effect after retraining",
            mapped_to="em.tf_freqs",
        )

    match_weights = {
        f: [
            math.log2(max(m, 1e-10) / max(u, 1e-10))
            for m, u in zip(m_probs[f], u_probs[f])
        ]
        for f in m_probs
    }

    if "probability_two_random_records_match" in settings:
        proportion_matched = settings["probability_two_random_records_match"]
    else:
        proportion_matched = 0.05
        report.info(
            "probability_two_random_records_match",
            "probability_two_random_records_match absent from trained settings; "
            "assumed default 0.05",
            mapped_to="em.proportion_matched",
        )

    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=True,
        iterations=0,
        proportion_matched=proportion_matched,
        tf_freqs=None,
        tf_collision=None,
    )


# ── Settings scalar mapping ──────────────────────────────────────────────────

_INFRA_IGNORED_KEYS = (
    "sql_dialect",
    "retain_matching_columns",
    "retain_intermediate_calculation_columns",
    "bayes_factor_column_prefix",
)


def convert_scalars(settings: dict, report: ConversionReport) -> dict:
    """Map top-level Splink settings scalars onto ``MatchkeyConfig`` kwargs.

    Returns a kwargs dict suitable for spreading into a ``MatchkeyConfig``
    constructor call; only keys actually present in ``settings`` are
    included. Everything not representable as a GoldenMatch config field
    (file paths, engine infra) is surfaced as a report finding instead of a
    kwarg -- Splink settings carry no file paths, so `unique_id_column_name`
    can only ever be advisory guidance for the caller's `InputConfig`.
    """
    kwargs: dict = {}

    if "em_convergence" in settings:
        kwargs["convergence_threshold"] = settings["em_convergence"]
        report.info(
            "em_convergence",
            f"em_convergence={settings['em_convergence']} -> convergence_threshold",
            mapped_to="matchkeys[?].convergence_threshold",
        )

    if "max_iterations" in settings:
        kwargs["em_iterations"] = settings["max_iterations"]
        report.info(
            "max_iterations",
            f"max_iterations={settings['max_iterations']} -> em_iterations",
            mapped_to="matchkeys[?].em_iterations",
        )

    if "unique_id_column_name" in settings:
        col = settings["unique_id_column_name"]
        report.info(
            "unique_id_column_name",
            f"unique_id_column_name={col!r} -> set input.files[*].id_column to "
            f"'{col}' (no InputConfig emitted; Splink settings carry no file "
            "paths)",
            mapped_to="input.files[*].id_column",
        )

    link_type = settings.get("link_type")
    if link_type == "link_and_dedupe":
        report.warn(
            "link_type",
            "link_type='link_and_dedupe' has no single GoldenMatch entry "
            "point -- run dedupe() on each source then match() across "
            "sources (or vice versa) and combine the results",
            mapped_to=None,
        )
    elif link_type in ("dedupe_only", "link_only"):
        entry_point = "dedupe()" if link_type == "dedupe_only" else "match()"
        report.info(
            "link_type",
            f"link_type='{link_type}' -> use GoldenMatch's {entry_point}",
            mapped_to=entry_point,
        )
    elif link_type is not None:
        report.info(
            "link_type",
            f"unrecognized link_type={link_type!r}, ignored",
            mapped_to=None,
        )

    for key in _INFRA_IGNORED_KEYS:
        if key in settings:
            report.info(key, f"'{key}' ignored (engine infra)", mapped_to=None)

    return kwargs


# ── Public entry point ───────────────────────────────────────────────────────

_MATCHKEY_NAME = "splink_import"


@dataclass
class SplinkConversion:
    """Result of :func:`from_splink`.

    ``em_model`` (when present) is an in-memory :class:`EMResult` only -- this
    library call never touches disk. Callers who want EM-skip-on-reuse
    behavior must persist it themselves (``em_model.save_json(path)``) and
    set the resulting path on ``config.matchkeys[0].model_path``. The CLI
    does this via ``--model-out``; the MCP surface deliberately does NOT
    persist -- it returns ``em_model`` inline instead (the remote surface is
    filesystem-free), leaving persistence to the caller.
    """

    config: GoldenMatchConfig
    report: ConversionReport
    em_model: EMResult | None


def _load_settings(source: dict | str | Path) -> dict:
    if isinstance(source, dict):
        return dict(source)  # shallow copy: never mutate the caller's dict

    if isinstance(source, (str, Path)):
        # safe_path is the repo-wide user-path choke-point (py/path-injection
        # mitigation): NUL-byte rejection always, containment under
        # GOLDENMATCH_ALLOWED_ROOT when configured (network-exposed surfaces).
        path = safe_path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SplinkConversionError(f"could not read Splink settings file {path}: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SplinkConversionError(f"malformed JSON in Splink settings file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise SplinkConversionError(
                f"Splink settings file {path} must contain a JSON object at the top "
                f"level, got {type(data).__name__}"
            )
        return data

    raise SplinkConversionError(
        f"from_splink() source must be a dict, str, or Path, got {type(source).__name__}"
    )


_PREVIEW_MAX_FINDINGS = 10


def _findings_preview(findings: list[ConversionFinding]) -> str:
    """Render findings for an exception message, capped at the first
    :data:`_PREVIEW_MAX_FINDINGS` so a pathological input can't produce a
    multi-page exception. The full report is on SplinkConversion.report (or
    re-runnable with strict=False).
    """
    shown = findings[:_PREVIEW_MAX_FINDINGS]
    preview = "; ".join(f"[{f.severity}] {f.splink_path}: {f.message}" for f in shown)
    remaining = len(findings) - len(shown)
    if remaining > 0:
        preview += (
            f"; ... and {remaining} more; rerun with strict=False for the full report"
        )
    return preview


def _patch_field_placeholders(report: ConversionReport, comp_path: str, field_idx: int) -> None:
    """Resolve the `matchkeys[?].fields[?]` placeholder `convert_comparison`
    leaves on its own findings, now that the field's final position in the
    assembled matchkey is known.
    """
    resolved = f"matchkeys[0].fields[{field_idx}]"
    for f in report.findings:
        if f.splink_path == comp_path and f.mapped_to and f.mapped_to.startswith(_PLACEHOLDER_PREFIX):
            f.mapped_to = resolved + f.mapped_to[len(_PLACEHOLDER_PREFIX):]


def from_splink(source: dict | str | Path, *, strict: bool = False) -> SplinkConversion:
    """Convert a Splink settings dict / JSON file into a GoldenMatch config.

    Args:
        source: A Splink settings dict, or a path (``str``/``Path``) to a
            JSON file containing one. Bare (untrained) or trained (carrying
            ``m_probability``/``u_probability``) settings are both accepted.
        strict: When True, ANY warning or error finding raises
            :class:`SplinkConversionError` (a fully lossless conversion is
            required). When False (default), only error-severity findings
            raise -- e.g. zero convertible comparisons or blocking rules.

    Returns:
        A :class:`SplinkConversion` with a validated ``GoldenMatchConfig``,
        the full ``ConversionReport``, and an ``EMResult`` when the input
        settings were trained (``None`` for bare settings). The library
        never persists the ``EMResult`` to disk -- see
        :class:`SplinkConversion`'s docstring.

    Raises:
        SplinkConversionError: on malformed input, zero convertible
            comparisons, zero convertible blocking rules, or (in ``strict``
            mode) any lossy finding.
    """
    settings = _load_settings(source)
    report = ConversionReport()

    survivors: list[tuple[dict, int, MatchkeyField]] = []
    for idx, comp in enumerate(settings.get("comparisons", [])):
        field = convert_comparison(comp, idx, report)
        if field is not None:
            survivors.append((comp, idx, field))

    if not survivors:
        report.error(
            "comparisons",
            "no comparison could be converted to a MatchkeyField",
            mapped_to=None,
        )
        raise SplinkConversionError(
            f"from_splink(): zero convertible comparisons -- {report.summary()}"
        )

    for field_idx, (comp, idx, _field) in enumerate(survivors):
        _patch_field_placeholders(report, f"comparisons[{idx}]", field_idx)

    blocking = convert_blocking(
        settings.get("blocking_rules_to_generate_predictions", []), report
    )
    if blocking is None:
        raise SplinkConversionError(
            f"from_splink(): zero convertible blocking rules -- {report.summary()}"
        )

    scalar_kwargs = convert_scalars(settings, report)

    mk = MatchkeyConfig(
        name=_MATCHKEY_NAME,
        type="probabilistic",
        fields=[field for _comp, _idx, field in survivors],
        **scalar_kwargs,
    )

    em_model = import_em(survivors, settings, report)

    # A ValidationError here is a bug in this converter (the emitted config
    # doesn't satisfy GoldenMatchConfig's own invariants) -- let it propagate
    # loudly rather than wrapping it in SplinkConversionError.
    config = GoldenMatchConfig(matchkeys=[mk], blocking=blocking)

    if strict and (report.has_warnings or report.has_errors):
        preview = _findings_preview(
            [f for f in report.findings if f.severity in ("warning", "error")]
        )
        raise SplinkConversionError(
            f"from_splink(strict=True): lossy conversion -- {report.summary()}. {preview}"
        )
    if report.has_errors:
        preview = _findings_preview(
            [f for f in report.findings if f.severity == "error"]
        )
        raise SplinkConversionError(
            f"from_splink(): conversion error -- {report.summary()}. {preview}"
        )

    return SplinkConversion(config=config, report=report, em_model=em_model)
