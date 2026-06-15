"""Pre-flight lint rules.

Each rule reuses the exact signals + thresholds the auto-config path already
trusts (cardinality_ratio >= 0.95 blocking skip in autoconfig.py; the planner's
SIMPLE_PLAN_MAX_PAIRS / 1M in-memory envelope) — so an explicit config gets the
same data-shape scrutiny the zero-config path gets for free.

Importing this module registers the rules into the registry.
"""
from __future__ import annotations

from goldenmatch.core.config_lint.registry import (
    LintInput,
    LintRule,
    RuleHit,
    Severity,
    register,
)

# Mirror of the auto-config constants (kept as literals here, asserted against
# the source in tests/test_config_lint.py so a future drift is caught).
_NEAR_UNIQUE_RATIO = 0.95          # autoconfig.py: blocking skips cols >= this
_NULL_HEAVY_RATE = 0.5             # a fuzzy field this null scores mostly sparse
_SIMPLE_PLAN_MAX_PAIRS = 50_000_000  # autoconfig_planner_rules.SIMPLE_PLAN_MAX_PAIRS
_INMEM_ROW_ENVELOPE = 1_000_000    # ~1M polars-direct is ~43min/10GB; beyond risks OOM
_INMEM_BACKENDS = frozenset({None, "polars-direct"})


# ── column extraction (duck-typed over GoldenMatchConfig) ───────────────────

def _blocking_keys(config: object) -> list[list[str]]:
    """Each blocking key/pass as its list of field names."""
    bl = getattr(config, "blocking", None)
    if bl is None:
        return []
    keys: list[list[str]] = []
    for group_attr in ("keys", "passes"):
        for k in (getattr(bl, group_attr, None) or []):
            fields = list(getattr(k, "fields", None) or [])
            if fields:
                keys.append(fields)
    return keys


def _fuzzy_field_columns(config: object) -> list[str]:
    mks = (config.get_matchkeys() if hasattr(config, "get_matchkeys")
           else (getattr(config, "matchkeys", None) or []))
    cols: list[str] = []
    for mk in mks:
        if getattr(mk, "type", None) in ("weighted", "probabilistic", "fuzzy"):
            for f in (getattr(mk, "fields", None) or []):
                c = getattr(f, "field", None) or getattr(f, "column", None)
                if c:
                    cols.append(c)
    return cols


def referenced_columns(config: object) -> set[str]:
    """Every column the config's blocking + fuzzy matchkeys reference."""
    cols: set[str] = set()
    for key in _blocking_keys(config):
        cols.update(key)
    cols.update(_fuzzy_field_columns(config))
    return cols


# ── rules ───────────────────────────────────────────────────────────────────

def _check_near_unique(config: object, inp: LintInput) -> list[RuleHit]:
    seen: set[str] = set()
    hits: list[RuleHit] = []
    for key in _blocking_keys(config):
        for col in key:
            if col in seen:
                continue
            seen.add(col)
            r = inp.cardinality_ratio.get(col)
            if r is not None and r >= _NEAR_UNIQUE_RATIO:
                hits.append(RuleHit(
                    message=f"blocking key '{col}' is near-unique (cardinality {r:.2f} >= {_NEAR_UNIQUE_RATIO}): most blocks are singletons, so true duplicates are never compared.",
                    target=col,
                    suggestion=f"block on a coarser key for '{col}' (e.g. a transform like soundex / substring) or a lower-cardinality field.",
                ))
    return hits


register(LintRule(
    id="blocking.near_unique",
    category="blocking",
    severity=Severity.WARN,
    title="Near-unique blocking key",
    fires_when="a blocking key column has cardinality_ratio >= 0.95",
    rationale=(
        "A blocking key that is almost unique puts (nearly) every record in its own block, "
        "so no candidate pairs are formed and recall collapses. The zero-config path skips "
        "such columns as blocking keys for exactly this reason; an explicit config does not, "
        "so it must be flagged before the run."
    ),
    needs=("cardinality_ratio",),
    check=_check_near_unique,
))


def _check_pair_explosion(config: object, inp: LintInput) -> list[RuleHit]:
    keys = _blocking_keys(config)
    if not keys or inp.row_count <= 1:
        return []
    n = inp.row_count
    # Per key, the most-selective field bounds block size from below (a composite
    # key is at least as selective as its best field). Estimate candidate pairs
    # as n^2 / (2 * n_distinct) and sum across keys/passes (a union adds pairs).
    est = 0.0
    for key in keys:
        ratios = [inp.cardinality_ratio.get(c) for c in key]
        ratios = [r for r in ratios if r is not None and r > 0]
        if not ratios:
            continue
        n_distinct = max(1.0, max(ratios) * n)
        est += (n * n) / (2.0 * n_distinct)
    if est > _SIMPLE_PLAN_MAX_PAIRS:
        return [RuleHit(
            message=(f"this blocking config is estimated to generate ~{est/1e6:.0f}M candidate pairs "
                     f"(> {_SIMPLE_PLAN_MAX_PAIRS//1_000_000}M) on {n:,} rows — likely slow and at OOM risk "
                     f"on the default in-memory backend."),
            target=None,
            suggestion="use a finer/more selective blocking key, or set backend='chunked'/'duckdb' (which the auto-planner would pick at this pair count).",
        )]
    return []


register(LintRule(
    id="blocking.pair_explosion",
    category="blocking",
    severity=Severity.WARN,
    title="Candidate-pair explosion",
    fires_when="estimated candidate pairs exceed SIMPLE_PLAN_MAX_PAIRS (50M)",
    rationale=(
        "A coarse blocking key produces a few enormous blocks whose intra-block comparison is "
        "O(n^2). The planner projects pair counts to choose a backend (chunked/duckdb above 50M); "
        "on the explicit path that projection never runs, so a config that will explode candidate "
        "pairs on the in-memory backend is flagged with the same 50M threshold."
    ),
    needs=("row_count", "cardinality_ratio"),
    check=_check_pair_explosion,
))


def _check_null_heavy(config: object, inp: LintInput) -> list[RuleHit]:
    hits: list[RuleHit] = []
    seen: set[str] = set()
    for col in _fuzzy_field_columns(config):
        if col in seen:
            continue
        seen.add(col)
        nr = inp.null_rate.get(col)
        if nr is not None and nr >= _NULL_HEAVY_RATE:
            hits.append(RuleHit(
                message=f"fuzzy matchkey field '{col}' is {nr:.0%} null: most pairs score on missing data, so this field contributes little signal.",
                target=col,
                suggestion=f"drop '{col}' from the matchkey, impute it, or rely on a less-null field.",
            ))
    return hits


register(LintRule(
    id="scoring.null_heavy_field",
    category="scoring",
    severity=Severity.WARN,
    title="Null-heavy fuzzy field",
    fires_when="a fuzzy matchkey field has null_rate >= 0.5",
    rationale=(
        "A fuzzy comparison field that is mostly null produces sparse scores: the weight is spent "
        "on a column that is absent for most pairs, diluting the combined score. The profiler tracks "
        "per-column null rate; this surfaces a heavily-null scoring field the explicit path would "
        "otherwise score silently."
    ),
    needs=("null_rate",),
    check=_check_null_heavy,
))


def _check_inmem_at_scale(config: object, inp: LintInput) -> list[RuleHit]:
    backend = getattr(config, "backend", None)
    if inp.row_count >= _INMEM_ROW_ENVELOPE and backend in _INMEM_BACKENDS:
        which = backend or "the default (polars-direct)"
        return [RuleHit(
            message=(f"{inp.row_count:,} rows with backend={which}: the in-memory path materializes "
                     f"score matrices on the driver and risks OOM at this scale."),
            target=None,
            suggestion="set backend='chunked' or 'duckdb', or run zero-config so the planner picks a scale-appropriate backend.",
        )]
    return []


register(LintRule(
    id="scale.inmemory_backend_at_scale",
    category="scale",
    severity=Severity.WARN,
    title="In-memory backend at scale",
    fires_when="row_count >= 1M and backend is unset or 'polars-direct'",
    rationale=(
        "The controller never runs on an explicit config, so its backend projection (which picks "
        "chunked/duckdb by rows/pairs/RAM) is skipped: an explicit config with no backend defaults "
        "to the in-memory polars-direct path, which at 1M+ rows is the documented OOM-risk envelope."
    ),
    needs=("row_count",),
    check=_check_inmem_at_scale,
))
