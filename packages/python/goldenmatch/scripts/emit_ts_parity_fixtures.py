"""Emit TypeScript parity fixtures for the auto-config controller (Wave 1).

Drives `AutoConfigController` on a curated set of mini-datasets and writes a
JSON file with the committed config, run history, complexity profile, and
final stop reason. The TS port at `packages/typescript/goldenmatch` consumes
this fixture in `tests/parity/controller-stoppoint.parity.test.ts` to verify
that the Wave-1 port matches Python v1.7/v1.8 behavior.

Usage::

    .venv/Scripts/python.exe \\
        packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py \\
        --out packages/typescript/goldenmatch/tests/parity/controller-stoppoint-fixtures.json

The script is a *dev tool*: it reads the Python `goldenmatch` runtime but
does not modify it. The output JSON is committed to the TS package so CI
does not need a Python interpreter to verify parity.

Fixture comparison contract (mirrors the TS test):

- ``committed_config`` — shape-level fields (matchkey names, threshold,
  blocking keys/fields/transforms). Numeric thresholds at 4dp.
- ``run_history.entries[*]`` — iteration, decision rule name (if any),
  health verdict.
- ``run_history.stop_reason`` — exact match.
- ``complexity_profile.data`` — n_rows, n_cols, column_types only (these
  are computable identically on the TS side from row dicts).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

# Make package importable when invoked from any cwd.
_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))

from goldenmatch.core.autoconfig_controller import (  # noqa: E402
    AutoConfigController,
    ControllerBudget,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy  # noqa: E402
from goldenmatch.core.indicators import (  # noqa: E402
    compute_column_priors,
    compute_corruption_score,
    compute_cross_blocking_overlap,
    estimate_full_pop_hits,
    estimate_sparse_match_signal,
)

# ---------------------------------------------------------------------------
# Curated mini-datasets
# ---------------------------------------------------------------------------

def _clean_people() -> list[dict]:
    """Distinct people, no dupes. Controller should commit GREEN quickly."""
    return [
        {"first": "Alice", "last": "Smith",   "email": "alice@example.com",  "zip": "10001"},
        {"first": "Bob",   "last": "Jones",   "email": "bob@example.com",    "zip": "10002"},
        {"first": "Carol", "last": "Davis",   "email": "carol@example.com",  "zip": "10003"},
        {"first": "David", "last": "Wilson",  "email": "david@example.com",  "zip": "10004"},
        {"first": "Eve",   "last": "Brown",   "email": "eve@example.com",    "zip": "10005"},
        {"first": "Frank", "last": "Miller",  "email": "frank@example.com",  "zip": "10006"},
    ]


def _sparse_people() -> list[dict]:
    """Mostly nulls — pathological-ish. Should fall back to YELLOW/RED."""
    return [
        {"first": "Alice", "last": None, "email": None,                "zip": None},
        {"first": None,    "last": "Jones", "email": None,             "zip": None},
        {"first": None,    "last": None, "email": "carol@example.com", "zip": None},
        {"first": None,    "last": None, "email": None,                "zip": "10004"},
    ]


def _dirty_people() -> list[dict]:
    """Same people with variations — typos/case. Real fuzzy dedup target."""
    return [
        {"first": "Alice",  "last": "Smith", "email": "alice@example.com", "zip": "10001"},
        {"first": "alice",  "last": "smith", "email": "ALICE@example.com", "zip": "10001"},
        {"first": "Alise",  "last": "Smyth", "email": "alise@example.com", "zip": "10001"},
        {"first": "Bob",    "last": "Jones", "email": "bob@example.com",   "zip": "10002"},
        {"first": "Bobby",  "last": "Jones", "email": "bob@example.com",   "zip": "10002"},
        {"first": "Robert", "last": "Jones", "email": "rob@example.com",   "zip": "10002"},
        {"first": "Carol",  "last": "Davis", "email": "carol@example.com", "zip": "10003"},
        {"first": "Karol",  "last": "Davis", "email": "karol@example.com", "zip": "10003"},
    ]


def _exact_id_people() -> list[dict]:
    """Strong exact identifier (email). Controller should commit exact-only."""
    return [
        {"name": "Alice",   "email": "alice@example.com",   "city": "NY"},
        {"name": "Alice S", "email": "alice@example.com",   "city": "NY"},
        {"name": "Bob",     "email": "bob@example.com",     "city": "LA"},
        {"name": "Carol",   "email": "carol@example.com",   "city": "SF"},
        {"name": "Carol",   "email": "carol@example.com",   "city": "SF"},
        {"name": "Dan",     "email": "dan@example.com",     "city": "TX"},
    ]


def _mixed_blocking() -> list[dict]:
    """Mixed shape with multiple plausible blocking keys."""
    return [
        {"first": "Alice", "last": "Smith", "phone": "5551234", "zip": "10001"},
        {"first": "Alice", "last": "Smyth", "phone": "5551234", "zip": "10001"},
        {"first": "Bob",   "last": "Jones", "phone": "5552222", "zip": "10002"},
        {"first": "Bob",   "last": "Jonss", "phone": "5552222", "zip": "10002"},
        {"first": "Carol", "last": "Davis", "phone": "5553333", "zip": "10003"},
        {"first": "Carolyn", "last": "Davis", "phone": "5553333", "zip": "10003"},
        {"first": "Dan",   "last": "White", "phone": "5554444", "zip": "10004"},
    ]


def _two_cluster() -> list[dict]:
    """Two clear clusters of duplicates, separated."""
    return [
        {"name": "Alice Smith",   "city": "New York", "phone": "555-1234"},
        {"name": "alice smith",   "city": "new york", "phone": "5551234"},
        {"name": "Alice E Smith", "city": "NY",       "phone": "(555) 123-4"},
        {"name": "Bob Jones",     "city": "Los Angeles", "phone": "555-9999"},
        {"name": "BOB JONES",     "city": "LA",          "phone": "5559999"},
        {"name": "Robert Jones",  "city": "LA",          "phone": "555-9999"},
    ]


DATASETS: dict[str, list[dict]] = {
    "clean_people":   _clean_people(),
    "sparse_people":  _sparse_people(),
    "dirty_people":   _dirty_people(),
    "exact_id":       _exact_id_people(),
    "mixed_blocking": _mixed_blocking(),
    "two_cluster":    _two_cluster(),
}


# ---------------------------------------------------------------------------
# Serializers (Python-side -> JSON-friendly dicts)
# ---------------------------------------------------------------------------

def _matchkey_dict(mk) -> dict[str, Any]:
    out: dict[str, Any] = {"name": mk.name, "type": mk.type}
    fields = []
    for f in (mk.fields or []):
        fields.append({
            "field": f.field,
            "transforms": list(f.transforms or []),
            "scorer": f.scorer,
            "weight": round(float(f.weight), 4) if f.weight is not None else None,
        })
    out["fields"] = fields
    threshold = getattr(mk, "threshold", None)
    if threshold is not None:
        out["threshold"] = round(float(threshold), 4)
    return out


def _blocking_dict(bk) -> dict[str, Any] | None:
    if bk is None:
        return None
    keys = [
        {"fields": list(k.fields or []), "transforms": list(k.transforms or [])}
        for k in (bk.keys or [])
    ]
    passes = [
        {"fields": list(k.fields or []), "transforms": list(k.transforms or [])}
        for k in (bk.passes or [])
    ] if bk.passes else None
    out: dict[str, Any] = {
        "strategy": bk.strategy,
        "keys": keys,
    }
    if passes:
        out["passes"] = passes
    return out


def _config_dict(cfg) -> dict[str, Any]:
    matchkeys = [_matchkey_dict(mk) for mk in (cfg.get_matchkeys() or [])]
    return {
        "matchkeys": matchkeys,
        "blocking": _blocking_dict(cfg.blocking),
    }


def _profile_data_dict(profile) -> dict[str, Any]:
    dp = profile.data
    return {
        "n_rows": dp.n_rows,
        "n_cols": dp.n_cols,
        "column_types": dict(dp.column_types),
    }


def _history_dict(history) -> dict[str, Any]:
    entries = []
    for e in history.entries:
        entry: dict[str, Any] = {
            "iteration": e.iteration,
            "health": e.profile.health().value if e.profile is not None else None,
            "error": e.error.exception_type if e.error else None,
        }
        if e.decision is not None:
            entry["decision_rule"] = e.decision.rule_name
        entries.append(entry)
    return {
        "entries": entries,
        "stop_reason": history.stop_reason.value if history.stop_reason else None,
        "n_entries": len(history.entries),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _indicators_dict(df: pl.DataFrame) -> dict[str, Any]:
    """Compute the 5 wave-2 indicators on `df` and return a JSON-serialisable dict."""
    priors = compute_column_priors(df)
    column_priors = {
        col: {
            "identity_score": round(float(cp.identity_score), 4),
            "corruption_score": round(float(cp.corruption_score), 4),
        }
        for col, cp in priors.items()
    }
    # estimate_sparse_match_signal default: exact_columns=[] -> always sparse.
    # For the fixture we feed the first text/email-like column if present.
    candidates = [c for c in df.columns if not c.startswith("__")]
    exact_candidates = [
        c for c in candidates if "email" in c.lower() or "id" in c.lower()
    ][:1]
    sv = estimate_sparse_match_signal(df, exact_columns=exact_candidates or [])
    sparsity = {
        "is_sparse": bool(sv.is_sparse),
        "estimated_n_true_pairs": int(sv.estimated_n_true_pairs),
    }
    # Per-column corruption: scalar map (mirrors Python public API).
    corruption_per_col = {
        col: round(float(compute_corruption_score(df, col)), 4)
        for col in candidates
    }
    # Full-pop hits on each candidate column.
    full_pop = {}
    for col in candidates:
        hits = estimate_full_pop_hits(df, col)
        full_pop[col] = None if hits is None else int(hits)
    # Cross-blocking overlap matrix (upper triangle) for first 4 candidates.
    overlap: dict[str, float | None] = {}
    short = candidates[:4]
    for i, a in enumerate(short):
        for b in short[i + 1:]:
            v = compute_cross_blocking_overlap(df, a, b)
            overlap[f"{a}|{b}"] = None if v is None else round(float(v), 4)
    return {
        "column_priors": column_priors,
        "sparsity": sparsity,
        "corruption_per_column": corruption_per_col,
        "full_pop_hits": full_pop,
        "cross_blocking_overlap": overlap,
    }


def _run_one(name: str, rows: list[dict]) -> dict[str, Any]:
    df = pl.DataFrame(rows)
    policy = HeuristicRefitPolicy()
    budget = ControllerBudget(max_iterations=3, max_seconds=30.0)
    ctrl = AutoConfigController(policy=policy, budget=budget, memory=None)
    try:
        committed_cfg, profile_full, history = ctrl.run(df, skip_finalize=True)
    except Exception as exc:  # surface controller errors as fixture entries too
        return {
            "name": name,
            "input_rows": rows,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "name": name,
        "input_rows": rows,
        "expected_committed_config": _config_dict(committed_cfg),
        "expected_run_history": _history_dict(history),
        "expected_complexity_profile": {"data": _profile_data_dict(profile_full)},
        "expected_stop_reason": history.stop_reason.value if history.stop_reason else None,
        "expected_indicators_profile": _indicators_dict(df),
    }


# ---------------------------------------------------------------------------
# Indicator-only fixtures (Task 2 Step 2)
# ---------------------------------------------------------------------------

def _id_high_prevalence() -> list[dict]:
    return [
        {"user_id": f"U{i:04d}", "name": f"Name{i}", "city": "NY"}
        for i in range(20)
    ]


def _all_null_col() -> list[dict]:
    return [
        {"name": "Alice", "phone": None, "city": "NY"},
        {"name": "Bob",   "phone": None, "city": "LA"},
        {"name": "Carol", "phone": None, "city": "SF"},
    ]


def _heavy_typos() -> list[dict]:
    return [
        {"name": "Alice", "email": "ALICE@example.com"},
        {"name": "alice", "email": "alice@Example.com"},
        {"name": "ALICE", "email": "alice@example.com"},
        {"name": "Bob",   "email": "bob@example.com"},
        {"name": "BOB",   "email": "BOB@example.com"},
    ]


def _low_overlap_blocking() -> list[dict]:
    return [
        {"city": "NY", "category": "A"},
        {"city": "NY", "category": "B"},
        {"city": "LA", "category": "A"},
        {"city": "LA", "category": "B"},
        {"city": "SF", "category": "C"},
        {"city": "SF", "category": "D"},
    ]


def _identity_collision() -> list[dict]:
    return [
        {"email": "share@example.com", "name": "Alice Anderson"},
        {"email": "share@example.com", "name": "Zachary Zykov"},
        {"email": "other@example.com", "name": "Bob"},
        {"email": "other@example.com", "name": "Bob B"},
    ]


def _dense_unique() -> list[dict]:
    return [
        {"first": f"Name{i}", "last": f"Last{i}", "email": f"u{i}@x.com"}
        for i in range(10)
    ]


def _sparse_minimal() -> list[dict]:
    return [
        {"a": "x", "b": None},
        {"a": None, "b": "y"},
        {"a": None, "b": None},
    ]


def _booleans_dates() -> list[dict]:
    return [
        {"name": "Alice", "active": True, "joined": "2024-01-01"},
        {"name": "Bob",   "active": False, "joined": "2024-02-02"},
        {"name": "Carol", "active": True, "joined": "2024-03-03"},
    ]


INDICATOR_DATASETS: dict[str, list[dict]] = {
    "id_high_prevalence": _id_high_prevalence(),
    "all_null_col": _all_null_col(),
    "heavy_typos": _heavy_typos(),
    "low_overlap_blocking": _low_overlap_blocking(),
    "identity_collision": _identity_collision(),
    "dense_unique": _dense_unique(),
    "sparse_minimal": _sparse_minimal(),
    "booleans_dates": _booleans_dates(),
}


def _run_indicator_one(name: str, rows: list[dict]) -> dict[str, Any]:
    df = pl.DataFrame(rows)
    return {
        "name": name,
        "input_rows": rows,
        "expected_indicators": _indicators_dict(df),
    }


# ---------------------------------------------------------------------------
# Wave 3 — negative-evidence parity fixtures
# ---------------------------------------------------------------------------

def _ne_clustered_email_diff_surname() -> list[dict]:
    """Shared-email pairs with conflicting surnames — Path Y target."""
    return [
        {"email": "share@x.com", "last_name": "Smith",      "first_name": "Alice"},
        {"email": "share@x.com", "last_name": "Smith",      "first_name": "Alice"},
        {"email": "share@x.com", "last_name": "Vanderbilt", "first_name": "Zach"},
        {"email": "other@x.com", "last_name": "Brown",      "first_name": "Bob"},
        {"email": "other@x.com", "last_name": "Brown",      "first_name": "Bob"},
    ]


def _ne_clustered_phone_diff_name() -> list[dict]:
    """Same shape but on phone instead of email."""
    return [
        {"phone": "555-1111", "last_name": "Smith",   "first_name": "Alice"},
        {"phone": "555-1111", "last_name": "Smith",   "first_name": "Alice"},
        {"phone": "555-1111", "last_name": "Zykov",   "first_name": "Zach"},
        {"phone": "555-2222", "last_name": "Brown",   "first_name": "Bob"},
        {"phone": "555-2222", "last_name": "Browne",  "first_name": "Bob"},
    ]


def _ne_dense_population() -> list[dict]:
    """Dense identity columns — promotion should add NE on phone."""
    return [
        {"email": f"u{i}@x.com", "phone": f"555-{i:04d}", "first_name": f"N{i}"}
        for i in range(12)
    ]


def _ne_sparse_no_promotion() -> list[dict]:
    """Sparse — no identity columns trigger NE promotion."""
    return [
        {"first_name": "Alice"},
        {"first_name": "Bob"},
        {"first_name": "Carol"},
    ]


def _ne_blocking_field_skipped() -> list[dict]:
    """Phone is in blocking, so it should NOT be promoted as NE."""
    return [
        {"email": "alice@x.com",  "phone": "555-1111", "last": "Smith"},
        {"email": "alice2@x.com", "phone": "555-1111", "last": "Smith"},
        {"email": "bob@x.com",    "phone": "555-2222", "last": "Brown"},
        {"email": "carol@x.com",  "phone": "555-3333", "last": "Davis"},
    ]


def _ne_idempotent() -> list[dict]:
    """Same as dense; we verify idempotency by running promote twice."""
    return [
        {"email": f"u{i}@x.com", "phone": f"555-{i:04d}", "last": f"L{i}"}
        for i in range(10)
    ]


NE_DATASETS: dict[str, list[dict]] = {
    "ne_clustered_email_diff_surname": _ne_clustered_email_diff_surname(),
    "ne_clustered_phone_diff_name":    _ne_clustered_phone_diff_name(),
    "ne_dense_population":             _ne_dense_population(),
    "ne_sparse_no_promotion":          _ne_sparse_no_promotion(),
    "ne_blocking_field_skipped":       _ne_blocking_field_skipped(),
    "ne_idempotent":                   _ne_idempotent(),
}


def _ne_field_dict(ne) -> dict[str, Any]:
    return {
        "field": ne.field,
        "transforms": list(ne.transforms or []),
        "scorer": ne.scorer,
        "threshold": round(float(ne.threshold), 4),
        "penalty": round(float(ne.penalty), 4),
    }


def _ne_matchkey_dict(mk) -> dict[str, Any]:
    out = _matchkey_dict(mk)
    ne_list = getattr(mk, "negative_evidence", None) or []
    out["negative_evidence"] = [_ne_field_dict(ne) for ne in ne_list]
    return out


def _run_ne_one(name: str, rows: list[dict]) -> dict[str, Any]:
    """Build a tiny zero-config, run promote_negative_evidence, emit before/after."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    from goldenmatch.core.indicators import compute_column_priors

    df = pl.DataFrame(rows)
    cols = [c for c in df.columns if not c.startswith("__")]

    # Build a tiny synthetic config: exact_<first-identity-col> + weighted fuzzy
    # over the remaining cols. This isolates NE behavior from the
    # auto_configure_df pipeline (which may differ across versions).
    identity_col: str | None = None
    for c in cols:
        cl = c.lower()
        if "email" in cl or "phone" in cl or "id" == cl:
            identity_col = c
            break
    blocking_fields: list[str] = []
    if name == "ne_blocking_field_skipped":
        blocking_fields = ["phone"]

    matchkeys: list[MatchkeyConfig] = []
    if identity_col is not None:
        matchkeys.append(
            MatchkeyConfig(
                name=f"exact_{identity_col}",
                type="exact",
                fields=[
                    MatchkeyField(
                        field=identity_col,
                        transforms=["lowercase", "strip"],
                        scorer="exact",
                        weight=1.0,
                    )
                ],
            )
        )
    weighted_fields = [
        MatchkeyField(
            field=c,
            transforms=["lowercase", "strip"],
            scorer="ensemble",
            weight=1.0,
        )
        for c in cols
        if c != identity_col
    ]
    if weighted_fields:
        matchkeys.append(
            MatchkeyConfig(
                name="weighted_fuzzy",
                type="weighted",
                fields=weighted_fields,
                threshold=0.85,
            )
        )

    if blocking_fields:
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=blocking_fields, transforms=[])],
        )
    else:
        # Pydantic requires a blocking config when weighted MKs exist; pick a
        # neutral default that doesn't affect NE promotion.
        first_col = cols[0]
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=[first_col], transforms=[])],
        )

    cfg = GoldenMatchConfig(matchkeys=matchkeys, blocking=blocking)

    priors = compute_column_priors(df)
    promoted = promote_negative_evidence(cfg, df, priors)
    # Idempotency check (re-run; expect no further changes).
    promoted_twice = promote_negative_evidence(promoted, df, priors)

    def _summary(c):  # serialize matchkeys-of-interest
        return {
            "matchkeys": [_ne_matchkey_dict(m) for m in c.matchkeys],
        }

    return {
        "name": name,
        "input_rows": rows,
        "before": _summary(cfg),
        "expected_after": _summary(promoted),
        "expected_after_idempotent": _summary(promoted_twice),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True,
        help="Output JSON path (typically tests/parity/controller-stoppoint-fixtures.json)",
    )
    parser.add_argument(
        "--indicators-out", type=Path, default=None,
        help="Optional path for indicator-only fixtures (wave-2). When set, "
             "emits a parallel JSON containing per-dataset indicator values.",
    )
    parser.add_argument(
        "--ne-out", type=Path, default=None,
        help="Optional path for wave-3 negative-evidence fixtures.",
    )
    args = parser.parse_args()

    payload: dict[str, dict] = {}
    for name, rows in DATASETS.items():
        print(f"  running {name} ({len(rows)} rows)...", file=sys.stderr)
        payload[name] = _run_one(name, rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {args.out} ({len(payload)} datasets)", file=sys.stderr)

    if args.indicators_out is not None:
        ind_payload: dict[str, dict] = {}
        for name, rows in INDICATOR_DATASETS.items():
            print(f"  indicators {name} ({len(rows)} rows)...", file=sys.stderr)
            ind_payload[name] = _run_indicator_one(name, rows)
        args.indicators_out.parent.mkdir(parents=True, exist_ok=True)
        args.indicators_out.write_text(
            json.dumps(ind_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            f"wrote {args.indicators_out} ({len(ind_payload)} indicator datasets)",
            file=sys.stderr,
        )

    if args.ne_out is not None:
        ne_payload: dict[str, dict] = {}
        for name, rows in NE_DATASETS.items():
            print(f"  ne {name} ({len(rows)} rows)...", file=sys.stderr)
            ne_payload[name] = _run_ne_one(name, rows)
        args.ne_out.parent.mkdir(parents=True, exist_ok=True)
        args.ne_out.write_text(
            json.dumps(ne_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            f"wrote {args.ne_out} ({len(ne_payload)} NE datasets)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
