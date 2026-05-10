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
    AutoConfigController, ControllerBudget,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy  # noqa: E402
from goldenmatch.core.complexity_profile import HealthVerdict  # noqa: E402


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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True,
        help="Output JSON path (typically tests/parity/controller-stoppoint-fixtures.json)",
    )
    args = parser.parse_args()

    payload: dict[str, dict] = {}
    for name, rows in DATASETS.items():
        print(f"  running {name} ({len(rows)} rows)...", file=sys.stderr)
        payload[name] = _run_one(name, rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {args.out} ({len(payload)} datasets)", file=sys.stderr)


if __name__ == "__main__":
    main()
