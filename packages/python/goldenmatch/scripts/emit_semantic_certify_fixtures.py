"""Emit the cross-language fixture that locks the TS semantic-model CERTIFIER
(consume side, wedge A) against Python `certify_semantic_model`.

The TS port (`packages/typescript/goldenmatch/src/core/semantic/{certify,cube,
osi,metricflow}.ts`) parses a dbt/MetricFlow, Cube, or OSI model and certifies
every key its metrics join on via the structural key-integrity tier. This script
runs the REAL Python `certify_semantic_model` over a spread of models + frames
(including models produced by the emitters, so parse(emit(...)) round-trips) and
records the resulting per-key certification, so a drift in the TS parser/certifier
fails the parity test.

The Python `certify_semantic_model` chain pulls only `pyarrow` + `yaml` + the
pure `KeyIntegrityCertificate` dataclass on the structural path, so we load the
emitter/certifier modules in isolation (stubbing the unused `blocking` module,
reached only by the Python-only `resolve=True` tier) rather than importing the
whole toolkit.

Run: `python3 scripts/emit_semantic_certify_fixtures.py`
Output: packages/typescript/goldenmatch/tests/parity/fixtures/semantic/certify.json
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pyarrow as pa
import yaml

_PKG = Path(__file__).resolve().parents[1] / "goldenmatch"
_OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "semantic"
    / "certify.json"
)


def _load(name: str, relpath: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PKG / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bootstrap() -> object:
    sys.modules.setdefault("goldenmatch", types.ModuleType("goldenmatch"))
    sys.modules.setdefault("goldenmatch.core", types.ModuleType("goldenmatch.core"))
    sys.modules.setdefault("goldenmatch.semantic", types.ModuleType("goldenmatch.semantic"))
    # blocking is imported unconditionally by certify_semantic_model but only CALLED
    # on the resolve=True metric-aware path (not exercised here) — a stub suffices.
    blocking = types.ModuleType("goldenmatch.semantic.blocking")
    blocking._frame_columns = lambda df: []  # type: ignore[attr-defined]
    blocking.metric_aware_attributes = lambda roles, cols: None  # type: ignore[attr-defined]
    blocking.semantic_field_roles = lambda data: None  # type: ignore[attr-defined]
    sys.modules["goldenmatch.semantic.blocking"] = blocking

    _load("goldenmatch.core.key_integrity_certificate", "core/key_integrity_certificate.py")
    _load("goldenmatch.semantic.key_integrity", "semantic/key_integrity.py")
    _load("goldenmatch.semantic.metricflow", "semantic/metricflow.py")
    _load("goldenmatch.semantic.cube", "semantic/cube.py")
    _load("goldenmatch.semantic.osi", "semantic/osi.py")
    # certify_semantic_model imports the feast + malloy dialects unconditionally
    # (both are Python-only / have no TS port, so they add no fixture case — but the
    # modules must resolve for certify.py to import in this isolated bootstrap).
    _load("goldenmatch.semantic.feast", "semantic/feast.py")
    _load("goldenmatch.semantic.malloy", "semantic/malloy.py")
    return _load("goldenmatch.semantic.certify", "semantic/certify.py").certify_semantic_model


_certify_semantic_model = _bootstrap()
# The emitters read a duck-typed stats object (`_StatsXW` below), so no real
# `ResolvedCrosswalk` is constructed here.
_emit_cube = sys.modules["goldenmatch.semantic.cube"].emit_cube_from_crosswalk
_emit_osi = sys.modules["goldenmatch.semantic.osi"].emit_osi_from_crosswalk


class _StatsXW:
    """Duck-typed stats-only crosswalk the emitters read (source_pk_column /
    resolved_key / n_records / n_entities / reduction_ratio)."""

    def __init__(self, source_pk_column: str, n_records: int, n_entities: int):
        self.source_pk_column = source_pk_column
        self.resolved_key = "resolved_entity_id"
        self.n_records = n_records
        self.n_entities = n_entities

    @property
    def reduction_ratio(self) -> float:
        if not self.n_records:
            return 0.0
        return 1.0 - (self.n_entities / self.n_records)


def _frames_to_arrow(frames: dict) -> dict:
    return {name: pa.table(cols) for name, cols in frames.items()}


def _serialize(report) -> dict:
    return {
        "dialect": report.dialect,
        "n_certified": report.n_certified,
        "all_trustworthy": report.all_trustworthy,
        "skipped": list(report.skipped),
        "entries": [
            {
                "target": e.target,
                "key": list(e.key),
                "context": e.context,
                "is_unique_at_grain": e.certificate.is_unique_at_grain,
                "max_fan_out": e.certificate.max_fan_out,
                "estimate": e.certificate.estimate,
                "measure_fan_out": dict(e.certificate.measure_fan_out),
            }
            for e in report.entries
        ],
    }


def _case(name: str, model: dict, frames: dict) -> dict:
    report = _certify_semantic_model(model, _frames_to_arrow(frames))
    return {"name": name, "model": model, "frames": frames, "expected": _serialize(report)}


def _cases() -> list[dict]:
    cases: list[dict] = []

    # --- metricflow -----------------------------------------------------------
    mf_model = {
        "semantic_models": [
            {
                "name": "orders",
                "model": "ref('orders')",
                "entities": [
                    {"name": "orders", "type": "primary", "expr": "resolved_entity_id"},
                    {"name": "customer_id", "type": "unique", "expr": "customer_id"},
                ],
                "measures": [{"name": "revenue", "agg": "sum", "expr": "revenue"}],
            }
        ]
    }
    cases.append(_case(
        "metricflow_unique",
        mf_model,
        {"orders": {"resolved_entity_id": ["e1", "e2", "e3"], "customer_id": ["1", "2", "3"], "revenue": [10, 20, 30]}},
    ))
    cases.append(_case(
        "metricflow_duplicated_key_fans_out",
        mf_model,
        {"orders": {"resolved_entity_id": ["e1", "e1", "e2"], "customer_id": ["1", "2", "3"], "revenue": [10, 30, 5]}},
    ))
    # A second model with no supplied frame is recorded in `skipped`.
    mf_two = {
        "semantic_models": [
            mf_model["semantic_models"][0],
            {"name": "customers", "entities": [{"name": "customers", "type": "primary", "expr": "resolved_entity_id"}]},
        ]
    }
    cases.append(_case(
        "metricflow_skips_model_without_frame",
        mf_two,
        {"orders": {"resolved_entity_id": ["e1", "e2"], "customer_id": ["1", "2"], "revenue": [1, 2]}},
    ))

    # --- cube (parse the emitter's own output -> certify) ---------------------
    cube_yaml = _emit_cube(_StatsXW("customer_id", 100, 80), source_cube="customers")
    cube_doc = yaml.safe_load(cube_yaml)
    cases.append(_case(
        "cube_from_emitter_unique",
        cube_doc,
        {"crosswalk": {"customer_id": ["1", "2", "3"], "source": ["crm", "crm", "crm"], "resolved_entity_id": ["e1", "e2", "e3"]}},
    ))
    cases.append(_case(
        "cube_from_emitter_duplicated",
        cube_doc,
        {"crosswalk": {"customer_id": ["1", "1", "2"], "source": ["crm", "crm", "crm"], "resolved_entity_id": ["e1", "e2", "e3"]}},
    ))
    # one_to_many: the DECLARING (from) cube is the one-side, so ITS key must be
    # unique — the opposite one-side selection from the many_to_one default. Locks
    # the TS member-ref parse + one-side selection against Python.
    cube_o2m = {
        "cubes": [
            {"name": "customer", "joins": [{"name": "orders", "relationship": "one_to_many", "sql": "{CUBE}.id = {orders.customer_id}"}]},
            {"name": "orders"},
        ]
    }
    cases.append(_case(
        "cube_one_to_many_certifies_from_cube_key",
        cube_o2m,
        {"customer": {"id": ["1", "2", "3"]}},
    ))
    cases.append(_case(
        "cube_one_to_many_duplicated_from_key",
        cube_o2m,
        {"customer": {"id": ["1", "1", "2"]}},
    ))

    # --- osi (parse the emitter's own output -> certify) ----------------------
    osi_yaml = _emit_osi(_StatsXW("customer_id", 100, 80), source_dataset="customers")
    osi_doc = yaml.safe_load(osi_yaml)
    cases.append(_case(
        "osi_from_emitter_unique",
        osi_doc,
        {"crosswalk": {"customer_id": ["1", "2", "3"], "source": ["crm", "crm", "crm"], "resolved_entity_id": ["e1", "e2", "e3"]}},
    ))
    cases.append(_case(
        "osi_from_emitter_duplicated",
        osi_doc,
        {"crosswalk": {"customer_id": ["9", "9", "9"], "source": ["crm", "crm", "crm"], "resolved_entity_id": ["e1", "e2", "e3"]}},
    ))

    return cases


def main() -> None:
    payload = {
        "_comment": (
            "Generated by scripts/emit_semantic_certify_fixtures.py. Locks the TS "
            "semantic-model certifier (consume side) against Python "
            "certify_semantic_model (structural tier). Do not hand-edit; regenerate."
        ),
        "cases": _cases(),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['cases'])} cases -> {_OUT}")


if __name__ == "__main__":
    main()
