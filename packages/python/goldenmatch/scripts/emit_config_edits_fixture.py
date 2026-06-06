"""Emit a cross-language parity fixture for the ConfigEdit lever vocabulary.

Applies a battery of edit specs (via ``edit_from_spec``) to a base config and
records, per case, whether the edit applied and a semantic projection of the
result (thresholds / types / scorers / weights / blocking). The TS port
(src/core/config-edits.ts) replays the same specs and must match.

Projection notes: scorers/weights are projected only for perturbable
(weighted/probabilistic) matchkeys -- Python's exact-matchkey fields carry
``None`` scorer/weight while the TS MatchkeyField requires both, so exact
fields are excluded from the comparison surface by design.

Output: packages/typescript/goldenmatch/tests/parity/fixtures/config-edits.json
Run:    .venv/Scripts/python.exe packages/python/goldenmatch/scripts/emit_config_edits_fixture.py
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.config_edits import edit_from_spec, fold_edits

_PERTURBABLE = ("weighted", "probabilistic")


def base_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="email", scorer="jaro_winkler", weight=0.8),
                ],
            ),
            MatchkeyConfig(
                name="email_exact",
                type="exact",
                fields=[MatchkeyField(field="email")],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[
                BlockingKeyConfig(fields=["email"], transforms=["lowercase"]),
                BlockingKeyConfig(fields=["zip"]),
            ],
        ),
    )


def project(cfg: GoldenMatchConfig) -> dict:
    mks = cfg.get_matchkeys()
    return {
        "thresholds": {mk.name: mk.threshold for mk in mks},
        "types": {mk.name: mk.type for mk in mks},
        "scorers": {
            f"{mk.name}.{f.field}": f.scorer
            for mk in mks if mk.type in _PERTURBABLE
            for f in (mk.fields or [])
        },
        "weights": {
            f"{mk.name}.{f.field}": f.weight
            for mk in mks if mk.type in _PERTURBABLE
            for f in (mk.fields or [])
        },
        "blocking_strategy": cfg.blocking.strategy if cfg.blocking else None,
        "blocking_keys": sorted(
            "+".join(k.fields) + "|" + ",".join(k.transforms or [])
            for k in (cfg.blocking.keys if cfg.blocking else [])
        ),
    }


CASES = [
    {"op": "threshold_shift", "delta": 0.05},
    {"op": "threshold_shift", "delta": 0.0},
    {"op": "threshold_shift", "delta": 0.5},
    {"op": "scorer_swap", "matchkey": "identity", "field": "name", "scorer": "token_sort"},
    {"op": "scorer_swap", "matchkey": "identity", "field": "name", "scorer": "jaro_winkler"},
    {"op": "scorer_swap", "matchkey": "identity", "field": "name", "scorer": "bogus_scorer"},
    {"op": "blocking_strategy", "strategy": "multi_pass"},
    {"op": "blocking_strategy", "strategy": "static"},
    {"op": "weight_shift", "matchkey": "identity", "field": "email", "delta": 0.2},
    {"op": "weight_shift", "matchkey": "identity", "field": "email", "delta": -2.0},
    {"op": "weight_shift", "matchkey": "email_exact", "field": "email", "delta": 0.1},
    {"op": "matchkey_type", "matchkey": "identity", "target_type": "probabilistic"},
    {"op": "matchkey_type", "matchkey": "identity", "target_type": "weighted"},
    {"op": "blocking_key", "action": "add", "fields": ["last_name"], "transforms": ["soundex"]},
    {"op": "blocking_key", "action": "add", "fields": ["email"], "transforms": ["lowercase"]},
    {"op": "blocking_key", "action": "remove", "fields": ["email"], "transforms": ["lowercase"]},
    {"op": "blocking_key", "action": "remove", "fields": ["city"]},
]

FOLD_SPECS = [
    {"op": "threshold_shift", "delta": 0.05},
    {"op": "weight_shift", "matchkey": "identity", "field": "email", "delta": 0.2},
    {"op": "blocking_strategy", "strategy": "multi_pass"},
    {"op": "scorer_swap", "matchkey": "identity", "field": "name", "scorer": "bogus_scorer"},
    # First remove applies (zip remains); the second would empty the keys ->
    # revalidation fails -> skipped. Locks the skip-invalid-edit semantics.
    {"op": "blocking_key", "action": "remove", "fields": ["email"], "transforms": ["lowercase"]},
    {"op": "blocking_key", "action": "remove", "fields": ["zip"]},
]


def main() -> None:
    cases_out = []
    for spec in CASES:
        edit = edit_from_spec(spec)
        assert edit is not None, f"spec failed to parse: {spec}"
        result = edit.apply(base_config())
        cases_out.append({
            "spec": spec,
            "label": edit.label,
            "applied": result is not None,
            "projection": project(result) if result is not None else None,
        })

    fold_edits_list = [e for e in (edit_from_spec(s) for s in FOLD_SPECS) if e is not None]
    folded = fold_edits(base_config(), fold_edits_list)

    fixture = {
        "cases": cases_out,
        "fold_case": {"specs": FOLD_SPECS, "projection": project(folded)},
        "base_projection": project(base_config()),
    }

    out = (
        Path(__file__).resolve().parents[3]
        / "typescript" / "goldenmatch" / "tests" / "parity" / "fixtures"
        / "config-edits.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, default=str))
    applied = sum(1 for c in cases_out if c["applied"])
    print(f"Wrote {out} ({len(cases_out)} cases, {applied} applied)")


if __name__ == "__main__":
    main()
