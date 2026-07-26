"""Hand-rolled dbt -> GoldenMatch config converter (distill + verify).

Spec: docs/superpowers/specs/2026-07-26-dbt-to-goldenmatch-converter-design.md

The value is DISTILLATION + PROOF, not faithful SQL translation. A ~10k-line
hand-rolled entity-resolution pipeline spread across dozens of dbt models
almost always encodes a *small* set of real decisions (a few blocking keys, a
handful of comparison fields + thresholds, some survivorship rules) buried in
sprawl. ``from_dbt`` reads the dbt ``manifest.json`` (structured metadata, NOT
blind ``.sql``), identifies the models that do entity resolution, extracts the
recognizable ER idioms into ONE ``GoldenMatchConfig``, and reports what it
could and could NOT extract. The optional ``verify_against_dbt`` measures how
faithfully the emitted config reproduces the pipeline's EXISTING output table
(label-free engine-vs-engine agreement, reusing the Splink verify math).

Design posture mirrors ``from_splink``: high fidelity on the recognized idiom
set, loud + honest (``couldnt_extract`` findings) about the unrecognized set,
verified against the user's own output. This module stays import-light +
polars-free (it only parses JSON + regexes SQL); ``verify_against_dbt`` lives in
``dbt_verify.py`` and pulls in the pipeline only when verification runs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Literal

from goldenmatch.config.from_splink import (
    _LEV_ASSUMED_LEN,
    ConversionReport,
)
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core._paths import safe_path


class DbtConversionError(ValueError):
    """Raised on a malformed manifest or (in strict mode) any lossy finding."""


# ── ER-model identification signals ──────────────────────────────────────────
#
# Case-insensitive substrings in a model NAME that hint at an entity-resolution
# / master-data / dedup model. These localize the ER step so transformation
# models that aren't ER are never analyzed.
_ER_NAME_SUBSTRINGS: tuple[str, ...] = (
    "dedup", "dedupe", "_dim", "dim_", "_master", "master_", "golden", "mdm",
    "identity", "_unique", "canonical", "_deduped", "resolve", "xref",
    "crosswalk", "survivor",
)

# The MVP dialect trio (dbt ``metadata.adapter_type``). Others fall through to a
# dialect-agnostic core (window-dedup + GROUP BY + the ANSI string funcs) and
# are flagged so the user knows recognition was partial.
_MVP_DIALECTS: frozenset[str] = frozenset({"duckdb", "snowflake", "bigquery"})

# Fuzzy similarity funcs -> (GoldenMatch scorer, scale). The literal threshold in
# the SQL is divided by ``scale`` (Snowflake's JAROWINKLER_SIMILARITY returns
# 0-100, DuckDB's jaro_winkler_similarity returns 0-1). Names are distinct
# across warehouses so a global table needs no per-dialect disambiguation.
_FUZZY_SIM_FUNCS: dict[str, tuple[str, float]] = {
    "jaro_winkler_similarity": ("jaro_winkler", 1.0),
    "jaro_winkler": ("jaro_winkler", 1.0),
    "jaro_similarity": ("jaro_winkler", 1.0),
    "jarowinkler_similarity": ("jaro_winkler", 100.0),  # snowflake, 0-100
    "jaccard": ("jaccard", 1.0),
}

# Edit-distance funcs -> GoldenMatch scorer. These are DISTANCES (smaller =
# closer), so a `<= k` predicate maps to a similarity via the same assumed-length
# approximation ``from_splink`` uses (sim = 1 - k/_LEV_ASSUMED_LEN).
_FUZZY_DIST_FUNCS: dict[str, str] = {
    "levenshtein": "levenshtein",
    "damerau_levenshtein": "levenshtein",
    "editdistance": "levenshtein",   # snowflake
    "edit_distance": "levenshtein",  # bigquery
}

# SQL string-normalization funcs -> GoldenMatch transform. INITCAP / REGEXP_*
# have no clean single-transform equivalent and are reported as couldnt_extract
# (the base column is still used).
_TRANSFORM_FUNCS: dict[str, str] = {
    "lower": "lowercase",
    "upper": "uppercase",
    "trim": "strip",
    "btrim": "strip",
    "ltrim": "strip",
    "rtrim": "strip",
}
_UNRECOGNIZED_WRAP_FUNCS: frozenset[str] = frozenset(
    {"initcap", "regexp_replace", "replace", "translate", "substr", "substring",
     "left", "right", "coalesce"}
)


SignalKind = Literal[
    "blocking", "exact_matchkey", "fuzzy_field", "transform", "survivorship",
    "couldnt_extract",
]


@dataclass
class RecognizedSignal:
    """One ER decision extracted from a dbt model's compiled SQL / metadata.

    Analogous to ``from_splink``'s ``RecognizedLevel``: the recognizer layer's
    unit of output, aggregated into the emitted config by :func:`from_dbt`.
    """

    kind: SignalKind
    columns: list[str]
    params: dict
    source_model: str
    confidence: float = 1.0
    sql_excerpt: str = ""


# ── SQL parsing helpers ──────────────────────────────────────────────────────


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split ``s`` on ``sep`` at paren depth 0 (so ``lower(a, b), c`` -> two)."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


_BARE_COL_RE = re.compile(r'^(?:[A-Za-z_]\w*\.)?["`]?([A-Za-z_]\w*)["`]?$')
_FUNC_WRAP_RE = re.compile(r'^([A-Za-z_]\w*)\s*\((.*)\)$', re.DOTALL)


def _unwrap_column(expr: str) -> tuple[str | None, list[str], list[str]]:
    """Peel normalization funcs off a column expression.

    ``lower(trim(a.email))`` -> ``("email", ["strip", "lowercase"], [])``. The
    transform list is in APPLY order (innermost SQL func first == GoldenMatch
    left-to-right). Returns ``(base_column, transforms, unrecognized_funcs)``;
    ``base_column`` is ``None`` when the expression isn't a (wrapped) single
    column (an arithmetic/CASE expression, a literal, ``*`` -- not extractable).
    """
    expr = expr.strip()
    transforms: list[str] = []       # outer-to-inner; reversed before return
    unrecognized: list[str] = []
    # Peel at most a few layers -- realistic normalization nests shallowly, and a
    # bound guards against pathological input.
    for _ in range(8):
        m = _BARE_COL_RE.match(expr)
        if m:
            transforms.reverse()  # outer-to-inner -> apply order (inner first)
            return m.group(1), transforms, unrecognized
        wrap = _FUNC_WRAP_RE.match(expr)
        if not wrap:
            return None, [], unrecognized
        func = wrap.group(1).lower()
        args = _split_top_level(wrap.group(2))
        if func in _TRANSFORM_FUNCS:
            # trim(a, 'x') / a normalize func with extra args isn't a plain wrap.
            if len(args) != 1:
                return None, [], unrecognized
            transforms.append(_TRANSFORM_FUNCS[func])
            expr = args[0].strip()
            continue
        if func in _UNRECOGNIZED_WRAP_FUNCS:
            # Wrapping we can't represent as a single transform. Keep peeling to
            # recover the base column (still usable as a blocking/match field);
            # record the func so the report flags the lost normalization.
            unrecognized.append(func)
            if len(args) >= 1 and _split_top_level(args[0]):
                expr = args[0].strip()
                continue
            return None, [], unrecognized
        # Unknown func -> not a recognizable column expression.
        return None, [], unrecognized
    return None, [], unrecognized


def _col_field(expr: str) -> str | None:
    """Base column name of a plain (optionally alias-qualified) column ref."""
    m = _BARE_COL_RE.match(expr.strip())
    return m.group(1) if m else None


# ── Idiom recognizers over compiled SQL ──────────────────────────────────────
#
# QUALIFY ROW_NUMBER() OVER (PARTITION BY <cols> [ORDER BY <col> [DESC]]) = 1
# -- the canonical window-dedup: keep one row per partition key. The partition
# columns are the natural key (blocking + exact matchkey); a DESC order-by date
# column is a most-recent survivorship rule.
_QUALIFY_ROWNUM_RE = re.compile(
    r'qualify\s+row_number\s*\(\s*\)\s+over\s*\(\s*'
    r'partition\s+by\s+(?P<partition>.+?)'
    r'(?:\s+order\s+by\s+(?P<order>.+?))?\s*\)\s*=\s*1',
    re.IGNORECASE | re.DOTALL,
)

# GROUP BY <natural key> -- collapse-by-key dedup (master-data style). Only
# treated as ER inside an already-identified ER model (GROUP BY is ubiquitous in
# non-ER rollups), so it carries lower confidence. Terminated by the next clause.
_GROUP_BY_RE = re.compile(
    r'group\s+by\s+(?P<cols>.+?)'
    r'(?=\s+(?:order\s+by|having|qualify|limit|window|union)\b|\s*\)|\s*$)',
    re.IGNORECASE | re.DOTALL,
)

# JOIN ... ON <a.col> = <b.col> equality conjuncts -- the blocking keys of a
# matching self-join (paired with a fuzzy predicate in WHERE/ON).
_ON_EQUALITY_RE = re.compile(
    r'([A-Za-z_]\w*)\.["`]?([A-Za-z_]\w*)["`]?\s*=\s*'
    r'([A-Za-z_]\w*)\.["`]?([A-Za-z_]\w*)["`]?',
    re.IGNORECASE,
)

# soundex(<a>) = soundex(<b>) -- phonetic equality, GoldenMatch soundex_match.
_SOUNDEX_EQ_RE = re.compile(
    r'soundex\s*\(\s*(?P<a>[^()]+?)\s*\)\s*=\s*soundex\s*\(\s*(?P<b>[^()]+?)\s*\)',
    re.IGNORECASE,
)

# generate_surrogate_key([...]) / surrogate_key([...]) in RAW (jinja) code -- the
# argument list is the natural key the surrogate is built from.
_SURROGATE_KEY_RE = re.compile(
    r'(?:dbt_utils\.)?(?:generate_surrogate_key|surrogate_key)\s*\(\s*\[(?P<args>[^\]]*)\]',
    re.IGNORECASE,
)

# dbt_utils.deduplicate(..., partition_by='a, b', order_by='c desc', ...) in RAW
# code -- partition_by is the natural key, order_by drives survivorship. The
# call arguments nest parens (`relation=ref("stg")`), so the arg list is scanned
# with a balanced-paren matcher, NOT a `.*?` regex (which stops at the first `)`).
_DEDUPLICATE_CALL = re.compile(r'dbt_utils\.deduplicate\s*\(', re.IGNORECASE)
_KWARG_RE = re.compile(
    r'''(\w+)\s*=\s*(['"])(?P<val>.*?)\2''', re.IGNORECASE | re.DOTALL,
)


def _balanced_args(text: str, open_idx: int) -> str:
    """Return the substring inside the parens opening at ``text[open_idx]``.

    ``open_idx`` points at the ``(``; scanning stops at its matching ``)`` even
    across nested parens. Returns the inner text (empty if unbalanced)."""
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return ""

_JINJA_STRING_LITERAL_RE = re.compile(r"""^['"](.*)['"]$""", re.DOTALL)


def _fuzzy_predicate_re(func: str) -> re.Pattern:
    """`func(<a>, <b>) <op> <literal>` recognizer for one fuzzy function."""
    return re.compile(
        re.escape(func) + r'\s*\(\s*(?P<a>[^(),]+?)\s*,\s*(?P<b>[^(),]+?)\s*\)'
        r'\s*(?P<op>>=|<=|>|<|=)\s*(?P<lit>[0-9]*\.?[0-9]+)',
        re.IGNORECASE,
    )


def _order_by_survivorship(order_expr: str, model: str) -> RecognizedSignal | None:
    """A DESC order-by date/updated column in a window-dedup is most-recent."""
    first = _split_top_level(order_expr)[:1]
    if not first:
        return None
    tokens = first[0].split()
    col = _col_field(tokens[0])
    if col is None:
        return None
    descending = len(tokens) > 1 and tokens[1].lower() == "desc"
    if not descending:
        # ASC / unspecified: the winner is the FIRST row, not most-recent -- we
        # can't name a survivorship strategy for it. Report, don't guess.
        return RecognizedSignal(
            "couldnt_extract", [col],
            {"reason": "window-dedup ORDER BY is not DESC; survivorship strategy "
                       "not inferred (would need per-column priority logic)"},
            model, confidence=0.3, sql_excerpt=order_expr.strip()[:200],
        )
    return RecognizedSignal(
        "survivorship", [col],
        {"strategy": "most_recent", "date_column": col},
        model, confidence=0.7, sql_excerpt=order_expr.strip()[:200],
    )


def _blocking_signal_from_cols(
    col_exprs: list[str], model: str, confidence: float, excerpt: str,
) -> tuple[RecognizedSignal | None, list[RecognizedSignal]]:
    """Turn a natural-key column list into a blocking signal (+ transform /
    couldnt_extract side signals). Returns ``(blocking_signal, extras)``."""
    fields: list[str] = []
    field_transforms: dict[str, list[str]] = {}
    extras: list[RecognizedSignal] = []
    for expr in col_exprs:
        base, transforms, unrecognized = _unwrap_column(expr)
        if base is None:
            extras.append(RecognizedSignal(
                "couldnt_extract", [],
                {"reason": f"could not extract a column from key expression {expr!r}"},
                model, confidence=0.2, sql_excerpt=expr[:200],
            ))
            continue
        fields.append(base)
        if transforms:
            field_transforms[base] = transforms
            extras.append(RecognizedSignal(
                "transform", [base], {"transforms": transforms},
                model, confidence=confidence, sql_excerpt=expr[:200],
            ))
        for func in unrecognized:
            extras.append(RecognizedSignal(
                "couldnt_extract", [base],
                {"reason": f"normalization {func}(...) on key column {base!r} has no "
                           "single GoldenMatch transform; base column used, "
                           "normalization dropped"},
                model, confidence=0.3, sql_excerpt=expr[:200],
            ))
    if not fields:
        return None, extras
    blocking = RecognizedSignal(
        "blocking", fields,
        {"field_transforms": field_transforms},
        model, confidence=confidence, sql_excerpt=excerpt[:200],
    )
    return blocking, extras


def extract_signals(
    node: dict, dialect: str, is_er: bool,
) -> list[RecognizedSignal]:
    """Recognize ER idioms in one model node's compiled + raw SQL.

    ``is_er`` gates the weaker GROUP BY signal (fires only inside an identified
    ER model). Unrecognized-but-ER-looking constructs are NOT emitted here as
    couldnt_extract wholesale (that would be noise); only the specific parse
    failures inside a recognized idiom are.
    """
    model = node.get("name", node.get("unique_id", "?"))
    compiled = node.get("compiled_code") or node.get("compiled_sql") or ""
    raw = node.get("raw_code") or node.get("raw_sql") or ""
    signals: list[RecognizedSignal] = []
    saw_window_key = False

    # 1. QUALIFY ROW_NUMBER window-dedup.
    for m in _QUALIFY_ROWNUM_RE.finditer(compiled):
        partition_cols = _split_top_level(m.group("partition"))
        blocking, extras = _blocking_signal_from_cols(
            partition_cols, model, 0.9, m.group(0),
        )
        if blocking is not None:
            signals.append(blocking)
            signals.append(RecognizedSignal(
                "exact_matchkey", blocking.columns, {}, model, 0.9,
                m.group(0)[:200],
            ))
            saw_window_key = True
        signals.extend(extras)
        order = m.group("order")
        if order:
            surv = _order_by_survivorship(order, model)
            if surv is not None:
                signals.append(surv)

    # 2. dbt_utils.deduplicate(...) in raw jinja.
    for m in _DEDUPLICATE_CALL.finditer(raw):
        args = _balanced_args(raw, m.end() - 1)
        kwargs = {km.group(1).lower(): km.group("val")
                  for km in _KWARG_RE.finditer(args)}
        pby = kwargs.get("partition_by")
        if pby:
            blocking, extras = _blocking_signal_from_cols(
                _split_top_level(pby), model, 0.9, m.group(0),
            )
            if blocking is not None:
                signals.append(blocking)
                signals.append(RecognizedSignal(
                    "exact_matchkey", blocking.columns, {}, model, 0.9,
                    m.group(0)[:200],
                ))
                saw_window_key = True
            signals.extend(extras)
        oby = kwargs.get("order_by")
        if oby:
            surv = _order_by_survivorship(oby, model)
            if surv is not None:
                signals.append(surv)

    # 3. generate_surrogate_key([...]) in raw jinja.
    for m in _SURROGATE_KEY_RE.finditer(raw):
        args = [
            (sm.group(1) if (sm := _JINJA_STRING_LITERAL_RE.match(a.strip())) else a.strip())
            for a in _split_top_level(m.group("args"))
        ]
        args = [a for a in args if a]
        if not args:
            continue
        blocking, extras = _blocking_signal_from_cols(args, model, 0.7, m.group(0))
        if blocking is not None:
            signals.append(blocking)
            signals.append(RecognizedSignal(
                "exact_matchkey", blocking.columns, {}, model, 0.7, m.group(0)[:200],
            ))
        signals.extend(extras)

    # 4. Fuzzy predicates (similarity + edit-distance funcs) + phonetic equality.
    fuzzy_seen: set[tuple[str, str]] = set()
    for func, (scorer, scale) in _FUZZY_SIM_FUNCS.items():
        for m in _fuzzy_predicate_re(func).finditer(compiled):
            sig = _fuzzy_from_match(m, scorer, model, scale=scale, distance=False)
            if sig is not None and (key := (sig.columns[0], scorer)) not in fuzzy_seen:
                fuzzy_seen.add(key)
                signals.append(sig)
    for func, scorer in _FUZZY_DIST_FUNCS.items():
        for m in _fuzzy_predicate_re(func).finditer(compiled):
            sig = _fuzzy_from_match(m, scorer, model, scale=1.0, distance=True)
            if sig is not None and (key := (sig.columns[0], scorer)) not in fuzzy_seen:
                fuzzy_seen.add(key)
                signals.append(sig)
    for m in _SOUNDEX_EQ_RE.finditer(compiled):
        col_a, col_b = _col_field(m.group("a")), _col_field(m.group("b"))
        if col_a and col_a == col_b and (col_a, "soundex_match") not in fuzzy_seen:
            fuzzy_seen.add((col_a, "soundex_match"))
            signals.append(RecognizedSignal(
                "fuzzy_field", [col_a],
                {"scorer": "soundex_match", "partial_threshold": None},
                model, 0.8, m.group(0)[:200],
            ))

    # 5. Self-join ON equalities -> blocking keys, but only when a fuzzy predicate
    #    is present (an ON-equality in a plain transform join is not a blocking
    #    key). Avoids treating every dimensional join as ER.
    if any(s.kind == "fuzzy_field" for s in signals) and not saw_window_key:
        on_cols: list[str] = []
        for m in _ON_EQUALITY_RE.finditer(compiled):
            la, lc, ra, rc = m.groups()
            if la != ra and lc == rc:  # a.col = b.col (distinct aliases, same col)
                on_cols.append(lc)
        on_cols = list(dict.fromkeys(on_cols))
        if on_cols:
            signals.append(RecognizedSignal(
                "blocking", on_cols, {"field_transforms": {}}, model, 0.7,
                "join ... on " + " and ".join(f"{c} = {c}" for c in on_cols),
            ))

    # 6. GROUP BY natural key -- weak signal, ER-model-gated.
    if is_er and not saw_window_key:
        for m in _GROUP_BY_RE.finditer(compiled):
            cols = _split_top_level(m.group("cols"))
            # Positional group-by (`group by 1, 2`) carries no column names.
            if any(re.fullmatch(r"\d+", c) for c in cols):
                signals.append(RecognizedSignal(
                    "couldnt_extract", [],
                    {"reason": "positional GROUP BY (group by 1, 2, ...) -- "
                               "column names not recoverable from the manifest"},
                    model, 0.3, m.group(0)[:200],
                ))
                continue
            blocking, extras = _blocking_signal_from_cols(cols, model, 0.5, m.group(0))
            if blocking is not None:
                signals.append(blocking)
                signals.append(RecognizedSignal(
                    "exact_matchkey", blocking.columns, {}, model, 0.5, m.group(0)[:200],
                ))
            signals.extend(extras)
            break  # one dedup GROUP BY per model is enough

    return signals


def _fuzzy_from_match(
    m: re.Match, scorer: str, model: str, *, scale: float, distance: bool,
) -> RecognizedSignal | None:
    """Build a fuzzy_field signal from a fuzzy-predicate regex match."""
    col_a, col_b = _col_field(m.group("a")), _col_field(m.group("b"))
    if not col_a or col_a != col_b:
        return None  # cross-column comparison -- not a single-field scorer
    literal = float(m.group("lit"))
    op = m.group("op")
    if distance:
        # `<= k` distance -> similarity via the assumed-length approximation.
        if op not in ("<=", "<"):
            return None
        threshold = max(0.0, 1.0 - literal / _LEV_ASSUMED_LEN)
    else:
        # `>= t` similarity -> threshold directly (scaled: Snowflake 0-100).
        if op not in (">=", ">"):
            return None
        threshold = literal / scale
    if not (0.0 < threshold <= 1.0):
        return None
    return RecognizedSignal(
        "fuzzy_field", [col_a],
        {"scorer": scorer, "partial_threshold": threshold,
         "approx": distance or scale != 1.0},
        model, 0.85, m.group(0)[:200],
    )


# ── ER-model identification ──────────────────────────────────────────────────


@dataclass
class ErModel:
    """An identified entity-resolution model, with the signals that fired."""

    unique_id: str
    name: str
    confidence: float
    reasons: list[str]


def _name_signal(name: str) -> str | None:
    low = name.lower()
    for sub in _ER_NAME_SUBSTRINGS:
        if sub in low:
            return sub
    return None


def _shape_signal(node: dict) -> list[str]:
    """Compiled/raw-SQL shape hints that a model does entity resolution."""
    compiled = (node.get("compiled_code") or node.get("compiled_sql") or "").lower()
    raw = (node.get("raw_code") or node.get("raw_sql") or "").lower()
    reasons: list[str] = []
    if _QUALIFY_ROWNUM_RE.search(compiled):
        reasons.append("window-dedup (QUALIFY ROW_NUMBER)")
    if "dbt_utils.deduplicate" in raw:
        reasons.append("dbt_utils.deduplicate")
    if _SURROGATE_KEY_RE.search(raw):
        reasons.append("generate_surrogate_key")
    for func in (*_FUZZY_SIM_FUNCS, *_FUZZY_DIST_FUNCS):
        if _fuzzy_predicate_re(func).search(compiled):
            reasons.append(f"fuzzy predicate ({func})")
            break
    if _SOUNDEX_EQ_RE.search(compiled):
        reasons.append("phonetic equality (soundex)")
    return reasons


def _unique_test_columns(manifest: dict) -> dict[str, list[str]]:
    """Map model unique_id -> columns carrying a dbt ``unique`` test.

    A ``unique`` test on a non-surrogate column is a strong "this is the
    identity key" hint. dbt test nodes carry ``test_metadata.name == 'unique'``
    plus ``attached_node`` (the model) and ``column_name``.
    """
    out: dict[str, list[str]] = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "test":
            continue
        meta = node.get("test_metadata") or {}
        if (meta.get("name") or "").lower() != "unique":
            continue
        attached = node.get("attached_node")
        col = node.get("column_name") or (meta.get("kwargs") or {}).get("column_name")
        if attached and col:
            out.setdefault(attached, []).append(col)
    return out


def identify_er_models(manifest: dict) -> list[ErModel]:
    """Rank the manifest's models by how strongly they look like ER models."""
    unique_tests = _unique_test_columns(manifest)
    models: list[ErModel] = []
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        name = node.get("name", uid)
        reasons: list[str] = []
        confidence = 0.0
        if (sub := _name_signal(name)) is not None:
            reasons.append(f"name contains '{sub}'")
            confidence = max(confidence, 0.5)
        shape = _shape_signal(node)
        if shape:
            reasons.extend(shape)
            confidence = max(confidence, 0.8)
        if uid in unique_tests:
            reasons.append(f"unique test on {unique_tests[uid]}")
            confidence = max(confidence, 0.6)
        if reasons:
            models.append(ErModel(uid, name, confidence, reasons))
    models.sort(key=lambda mdl: (-mdl.confidence, mdl.name))
    return models


# ── Coverage scorecard ───────────────────────────────────────────────────────


@dataclass
class DbtConversionCoverage:
    """A scannable scorecard for a dbt->GoldenMatch conversion.

    Analogous to ``from_splink``'s ``CoverageSummary``: how many models were
    analyzed, what the emitted config contains, how many constructs were flagged
    for review, and which value STORY applies (fuzzy-ER consolidation vs
    exact-dedup consolidation) -- so the report never over-claims an F1 win on a
    pure exact-dedup project.
    """

    total_models: int
    er_models_analyzed: int
    blocking_keys: int
    exact_matchkeys: int
    fuzzy_fields: int
    transforms: int
    survivorship_rules: int
    couldnt_extract: int

    @property
    def story(self) -> Literal["fuzzy-er", "exact-dedup", "none"]:
        if self.fuzzy_fields > 0:
            return "fuzzy-er"
        if self.exact_matchkeys > 0 or self.blocking_keys > 0:
            return "exact-dedup"
        return "none"

    @property
    def has_config(self) -> bool:
        """True when at least one blocking key or matchkey was extracted."""
        return self.blocking_keys > 0 or self.exact_matchkeys > 0 or self.fuzzy_fields > 0

    def line(self) -> str:
        """One-line human summary."""
        story_text = {
            "fuzzy-er": "fuzzy-ER consolidation (accuracy + consolidation)",
            "exact-dedup": "exact-dedup consolidation (maintainability, not an F1 story)",
            "none": "no entity-resolution logic recognized",
        }[self.story]
        parts = [
            f"{self.er_models_analyzed}/{self.total_models} models analyzed as ER",
            f"{self.blocking_keys} blocking key(s)",
            f"{self.exact_matchkeys} exact + {self.fuzzy_fields} fuzzy comparison field(s)",
            f"{self.survivorship_rules} survivorship rule(s)",
        ]
        if self.couldnt_extract:
            parts.append(f"{self.couldnt_extract} construct(s) flagged for review")
        return " -- ".join(parts) + f" -- story: {story_text}"


@dataclass
class DbtConversion:
    """Result of :func:`from_dbt`.

    ``config`` is ``None`` when no ER logic could be extracted (a non-ER
    project) -- the report + coverage still describe what was seen, so a caller
    can distinguish "empty project" from a crash. ``signals`` is the full list
    of recognized (and couldnt_extract) signals for auditing; ``er_models`` is
    the ranked identification result.
    """

    config: GoldenMatchConfig | None
    report: ConversionReport
    coverage: DbtConversionCoverage
    er_models: list[ErModel] = dc_field(default_factory=list)
    signals: list[RecognizedSignal] = dc_field(default_factory=list)


def _load_manifest(source: dict | str | Path) -> dict:
    if isinstance(source, dict):
        return source
    path = safe_path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DbtConversionError(f"could not read dbt manifest {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DbtConversionError(f"malformed JSON in dbt manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DbtConversionError(
            f"dbt manifest {path} must contain a JSON object at the top level, "
            f"got {type(data).__name__}"
        )
    return data


def _manifest_dialect(manifest: dict, report: ConversionReport) -> str:
    adapter = str((manifest.get("metadata") or {}).get("adapter_type") or "").lower()
    if not adapter:
        report.info("metadata", "manifest declares no adapter_type; using the "
                    "dialect-agnostic recognizer core", mapped_to=None)
        return "unknown"
    if adapter not in _MVP_DIALECTS:
        report.warn(
            "metadata",
            f"adapter '{adapter}' is outside the MVP dialect trio "
            f"{sorted(_MVP_DIALECTS)}; recognition falls through to the "
            "dialect-agnostic core (window-dedup + GROUP BY + ANSI string funcs) "
            "and warehouse-specific idioms may be missed",
            mapped_to=None,
        )
    return adapter


def from_dbt(
    source: dict | str | Path,
    *,
    strict: bool = False,
    min_confidence: float = 0.5,
) -> DbtConversion:
    """Distill a dbt project's hand-rolled ER logic into a GoldenMatch config.

    Args:
        source: a dbt ``manifest.json`` path (``str``/``Path``) or an
            already-parsed manifest dict (produced by ``dbt compile`` /
            ``dbt parse`` / ``dbt docs generate``).
        strict: when True, ANY warning or error finding raises
            :class:`DbtConversionError`. When False (default), only a malformed
            manifest raises -- a partial extraction is the expected outcome.
        min_confidence: models identified below this ER-confidence are reported
            (in ``er_models``) but NOT analyzed for signal extraction.

    Returns:
        A :class:`DbtConversion` with a validated ``GoldenMatchConfig`` (or
        ``None`` when nothing ER-shaped was found), the ``ConversionReport``,
        a ``DbtConversionCoverage`` scorecard, the ranked ER-model list, and
        every recognized signal.
    """
    manifest = _load_manifest(source)
    report = ConversionReport()
    dialect = _manifest_dialect(manifest, report)

    total_models = sum(
        1 for n in manifest.get("nodes", {}).values()
        if n.get("resource_type") == "model"
    )
    er_models = identify_er_models(manifest)
    analyzed = [m for m in er_models if m.confidence >= min_confidence]
    for m in er_models:
        if m.confidence >= min_confidence:
            report.info(
                f"model:{m.name}",
                f"identified as ER (confidence {m.confidence:.2f}): "
                + "; ".join(m.reasons),
                mapped_to=None,
            )
        else:
            report.info(
                f"model:{m.name}",
                f"low-confidence ER candidate ({m.confidence:.2f}), NOT analyzed: "
                + "; ".join(m.reasons),
                mapped_to=None,
            )

    nodes = manifest.get("nodes", {})
    all_signals: list[RecognizedSignal] = []
    for m in analyzed:
        node = nodes.get(m.unique_id, {})
        all_signals.extend(extract_signals(node, dialect, is_er=True))

    config = _emit_config(all_signals, report)

    coverage = _build_coverage(total_models, len(analyzed), all_signals, config)

    for sig in all_signals:
        if sig.kind == "couldnt_extract":
            report.warn(
                f"model:{sig.source_model}",
                f"couldn't extract: {sig.params.get('reason', 'unrecognized construct')}"
                + (f" ({sig.sql_excerpt})" if sig.sql_excerpt else ""),
                mapped_to=None,
            )

    if config is None:
        report.warn(
            "manifest",
            "no entity-resolution logic could be extracted into a config; the "
            "project may not do ER, or its idioms are outside the recognized set "
            "(see couldn't-extract findings)",
            mapped_to=None,
        )

    if strict and (report.has_warnings or report.has_errors):
        raise DbtConversionError(
            f"from_dbt(strict=True): lossy conversion -- {report.summary()}"
        )

    return DbtConversion(
        config=config,
        report=report,
        coverage=coverage,
        er_models=er_models,
        signals=all_signals,
    )


def _emit_config(
    signals: list[RecognizedSignal], report: ConversionReport,
) -> GoldenMatchConfig | None:
    """Aggregate recognized signals across ER models into ONE config."""
    # Blocking keys: de-duplicate by (fields tuple), union field_transforms.
    blocking_keys: list[BlockingKeyConfig] = []
    seen_block: set[tuple[str, ...]] = set()
    for sig in signals:
        if sig.kind != "blocking" or not sig.columns:
            continue
        key = tuple(sig.columns)
        if key in seen_block:
            continue
        seen_block.add(key)
        ft = {k: v for k, v in (sig.params.get("field_transforms") or {}).items() if v}
        blocking_keys.append(BlockingKeyConfig(
            fields=list(sig.columns), transforms=[], field_transforms=ft,
        ))

    # Exact matchkey fields: union of all exact-matchkey column sets.
    exact_fields: list[str] = []
    for sig in signals:
        if sig.kind != "exact_matchkey":
            continue
        for col in sig.columns:
            if col not in exact_fields:
                exact_fields.append(col)

    # Fuzzy fields: one per (column, scorer); on a threshold conflict keep the
    # LOOSER (lower) threshold and warn -- the numeric_diff-collapse precedent.
    fuzzy: dict[tuple[str, str], float | None] = {}
    for sig in signals:
        if sig.kind != "fuzzy_field":
            continue
        col = sig.columns[0]
        scorer = sig.params["scorer"]
        thr = sig.params.get("partial_threshold")
        k = (col, scorer)
        if k in fuzzy:
            prev = fuzzy[k]
            if prev is not None and thr is not None and abs(prev - thr) > 1e-9:
                looser = min(prev, thr)
                report.warn(
                    f"field:{col}",
                    f"field '{col}' fuzzy-matched by '{scorer}' at two thresholds "
                    f"({prev}, {thr}) across models; keeping the looser ({looser})",
                    mapped_to=None,
                )
                fuzzy[k] = looser
        else:
            fuzzy[k] = thr

    matchkeys: list[MatchkeyConfig] = []
    if exact_fields:
        matchkeys.append(MatchkeyConfig(
            name="dbt_exact",
            type="exact",
            fields=[MatchkeyField(field=f) for f in exact_fields],
        ))
    for (col, scorer), thr in fuzzy.items():
        # A single-field weighted matchkey: the field score (weight 1.0) is
        # compared directly to the matchkey threshold, reproducing the SQL
        # predicate `scorer(col) >= thr`. soundex_match is binary (no threshold);
        # default it to 1.0 (phonetic-equal).
        threshold = thr if thr is not None else 1.0
        matchkeys.append(MatchkeyConfig(
            name=f"dbt_fuzzy_{col}",
            type="weighted",
            threshold=threshold,
            fields=[MatchkeyField(
                field=col, scorer=scorer, weight=1.0, partial_threshold=threshold,
            )],
        ))

    if not matchkeys and not blocking_keys:
        return None

    blocking: BlockingConfig | None = None
    if blocking_keys:
        if len(blocking_keys) == 1:
            blocking = BlockingConfig(strategy="static", keys=blocking_keys)
        else:
            blocking = BlockingConfig(
                strategy="multi_pass", keys=blocking_keys, passes=blocking_keys,
            )
    elif any(mk.type == "weighted" for mk in matchkeys):
        # A weighted matchkey needs blocking; none was discovered -> block on the
        # exact fields if any, else warn (the pipeline auto-blocks at runtime).
        if exact_fields:
            blocking = BlockingConfig(
                strategy="static",
                keys=[BlockingKeyConfig(fields=exact_fields, transforms=[])],
            )
        else:
            report.warn(
                "blocking",
                "a fuzzy comparison was extracted but no blocking key was found; "
                "the emitted config relies on GoldenMatch's auto-blocking",
                mapped_to=None,
            )

    # Survivorship. A window-dedup's `ORDER BY <date> DESC` is a most-recent
    # rule, but GoldenMatch applies `most_recent` PER FIELD (each needs its
    # date_column) -- there is no runnable global-default form (the golden-record
    # builder constructs the default rule from `default_strategy` alone, dropping
    # the date_column). We don't read the column list at convert time, so we
    # REPORT the recognized rule + the exact remediation instead of emitting an
    # un-runnable `golden_rules.default`. It still counts toward the coverage
    # scorecard (a distilled decision), just isn't auto-wired into the config.
    surv = next((s for s in signals if s.kind == "survivorship"), None)
    if surv is not None and surv.params.get("strategy") == "most_recent":
        date_col = surv.params["date_column"]
        report.info(
            f"model:{surv.source_model}",
            f"recognized most-recent survivorship (keep the latest row by "
            f"'{date_col}'). GoldenMatch applies most_recent per field, so add a "
            f"golden_rules.field_rules entry for each surviving column: "
            f"{{strategy: most_recent, date_column: {date_col}}} "
            "(not auto-wired -- the converter doesn't read the column list).",
            mapped_to="golden_rules.field_rules",
        )

    return GoldenMatchConfig(
        matchkeys=matchkeys or None,
        blocking=blocking,
    )


def _build_coverage(
    total_models: int,
    er_analyzed: int,
    signals: list[RecognizedSignal],
    config: GoldenMatchConfig | None,
) -> DbtConversionCoverage:
    blocking_keys = 0
    exact = 0
    fuzzy = 0
    if config is not None:
        blocking_keys = len(config.blocking.keys) if config.blocking else 0
        for mk in config.matchkeys or []:
            if mk.type == "exact":
                exact += len(mk.fields)
            elif mk.type == "weighted":
                fuzzy += len(mk.fields)
    transforms = sum(1 for s in signals if s.kind == "transform")
    survivorship = sum(1 for s in signals if s.kind == "survivorship")
    couldnt = sum(1 for s in signals if s.kind == "couldnt_extract")
    return DbtConversionCoverage(
        total_models=total_models,
        er_models_analyzed=er_analyzed,
        blocking_keys=blocking_keys,
        exact_matchkeys=exact,
        fuzzy_fields=fuzzy,
        transforms=transforms,
        survivorship_rules=survivorship,
        couldnt_extract=couldnt,
    )
