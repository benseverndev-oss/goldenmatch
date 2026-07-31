"""Emit the cross-language fixture that locks the TS semantic catalog emitters
byte-for-byte against Python `yaml.safe_dump`.

The TS port (`packages/typescript/goldenmatch/src/core/semantic/{metricflow,cube,
osi,catalog}.ts` + `yamlEmit.ts`) reproduces the dialect emitters and a
PyYAML-`safe_dump`-compatible block-YAML serializer. This script builds a
stats-only `ResolvedCrosswalk` (exactly what `emit_semantic_model_from_store`
stands up) and emits each dialect over a spread of inputs — including PyYAML
scalar-quoting edge cases (`on`/`123`/`ref('x')`/`{CUBE}...` SQL) and the
integer-float `reduction_ratio` (`0.0`/`0.2`) — so a drift in the TS serializer
fails the parity test.

Run: `python3 scripts/emit_semantic_catalog_fixtures.py`
Output: packages/typescript/goldenmatch/tests/parity/fixtures/semantic/catalog-emit.json
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pyarrow as pa

try:  # normal path (CI / any env with goldenmatch fully installed)
    from goldenmatch.semantic.crosswalk import ResolvedCrosswalk
    from goldenmatch.semantic.cube import emit_cube_from_crosswalk
    from goldenmatch.semantic.metricflow import emit_from_crosswalk
    from goldenmatch.semantic.osi import emit_osi_from_crosswalk
except Exception:
    # Fallback: load the pure semantic emitter modules in isolation. They depend
    # only on `yaml` / `pyarrow` / dataclasses, but importing them the normal way
    # triggers `goldenmatch/__init__.py`, which eagerly pulls the whole toolkit
    # (numpy, goldenphonetic, ...). When those heavy deps are absent, register
    # lightweight parent-package placeholders + a stub for the unused
    # `key_integrity._to_arrow` (referenced only by `build_resolved_crosswalk`,
    # which this script does not call), then exec each emitter file directly so
    # the emit logic is the REAL Python source.
    _PKG = Path(__file__).resolve().parents[1] / "goldenmatch"

    def _load(name: str, relpath: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(name, _PKG / relpath)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    sys.modules.setdefault("goldenmatch", types.ModuleType("goldenmatch"))
    sys.modules.setdefault("goldenmatch.semantic", types.ModuleType("goldenmatch.semantic"))
    _ki = types.ModuleType("goldenmatch.semantic.key_integrity")
    _ki._to_arrow = lambda x: x  # type: ignore[attr-defined]  # unused by the emitters
    sys.modules["goldenmatch.semantic.key_integrity"] = _ki

    ResolvedCrosswalk = _load("goldenmatch.semantic.crosswalk", "semantic/crosswalk.py").ResolvedCrosswalk
    _load("goldenmatch.semantic.metricflow", "semantic/metricflow.py")
    emit_from_crosswalk = sys.modules["goldenmatch.semantic.metricflow"].emit_from_crosswalk
    emit_cube_from_crosswalk = _load("goldenmatch.semantic.cube", "semantic/cube.py").emit_cube_from_crosswalk
    emit_osi_from_crosswalk = _load("goldenmatch.semantic.osi", "semantic/osi.py").emit_osi_from_crosswalk

_OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "semantic"
    / "catalog-emit.json"
)


def _xw(
    *,
    source: str,
    source_pk_column: str,
    resolved_key: str = "resolved_entity_id",
    n_records: int = 0,
    n_entities: int = 0,
) -> ResolvedCrosswalk:
    """A stats-only crosswalk — the exact stand-in emit_semantic_model_from_store
    builds (empty table; emitters read only provenance stats)."""
    return ResolvedCrosswalk(
        table=pa.table({"source": [], "source_pk": [], resolved_key: []}),
        source=source,
        source_pk_column=source_pk_column,
        resolved_key=resolved_key,
        n_records=n_records,
        n_entities=n_entities,
    )


def _cases() -> list[dict]:
    cases: list[dict] = []

    # A representative crosswalk (100 records collapse to 80 entities -> 0.2).
    base = dict(source="crm", source_pk_column="customer_id", n_records=100, n_entities=80)

    # --- metricflow -----------------------------------------------------------
    cases.append({
        "name": "metricflow_basic",
        "dialect": "metricflow",
        "crosswalk": {**base},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_from_crosswalk(_xw(**base), "customers"),
    })
    cases.append({
        "name": "metricflow_with_measures_and_grain",
        "dialect": "metricflow",
        "crosswalk": {**base},
        "source_target": "orders",
        "emit": {"measures": ["revenue", "quantity"], "grain": "order_date"},
        "yaml": emit_from_crosswalk(
            _xw(**base), "orders", measures=["revenue", "quantity"], grain="order_date"
        ),
    })
    cases.append({
        "name": "metricflow_grain_list_and_entity_and_ref",
        "dialect": "metricflow",
        "crosswalk": {**base},
        "source_target": "customers",
        "emit": {
            "entity_name": "customer",
            "grain": ["signup_date"],
            "model_ref": "ref('dim_customers')",
        },
        "yaml": emit_from_crosswalk(
            _xw(**base),
            "customers",
            entity_name="customer",
            grain=["signup_date"],
            model_ref="ref('dim_customers')",
        ),
    })
    # source_pk == resolved_key: no `unique` source entity is emitted.
    same = dict(source="crm", source_pk_column="resolved_entity_id", n_records=10, n_entities=10)
    cases.append({
        "name": "metricflow_source_key_equals_resolved",
        "dialect": "metricflow",
        "crosswalk": {**same},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_from_crosswalk(_xw(**same), "customers"),
    })
    # Quoting edge cases: a source_pk that YAML would read as an int/bool, and a
    # resolved_key that reads as a bool. PyYAML single-quotes both.
    quoty = dict(source="on", source_pk_column="123", resolved_key="yes", n_records=5, n_entities=4)
    cases.append({
        "name": "metricflow_quoting_edges",
        "dialect": "metricflow",
        "crosswalk": {**quoty},
        "source_target": "no",
        "emit": {},
        "yaml": emit_from_crosswalk(_xw(**quoty), "no"),
    })

    # --- cube -----------------------------------------------------------------
    cases.append({
        "name": "cube_basic",
        "dialect": "cube",
        "crosswalk": {**base},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_cube_from_crosswalk(_xw(**base), source_cube="customers"),
    })
    # n_records=0 -> reduction_ratio 0.0 (integer float keeps its `.0`).
    zero = dict(source="crm", source_pk_column="customer_id", n_records=0, n_entities=0)
    cases.append({
        "name": "cube_zero_records",
        "dialect": "cube",
        "crosswalk": {**zero},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_cube_from_crosswalk(_xw(**zero), source_cube="customers"),
    })

    # --- osi ------------------------------------------------------------------
    cases.append({
        "name": "osi_basic",
        "dialect": "osi",
        "crosswalk": {**base},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_osi_from_crosswalk(_xw(**base), source_dataset="customers"),
    })
    # A reduction_ratio that needs 6-dp rounding (100/77 -> 0.23).
    odd = dict(source="crm", source_pk_column="customer_id", n_records=100, n_entities=77)
    cases.append({
        "name": "osi_rounded_ratio",
        "dialect": "osi",
        "crosswalk": {**odd},
        "source_target": "customers",
        "emit": {},
        "yaml": emit_osi_from_crosswalk(_xw(**odd), source_dataset="customers"),
    })

    return cases


def main() -> None:
    payload = {
        "_comment": (
            "Generated by scripts/emit_semantic_catalog_fixtures.py. Locks the TS "
            "semantic catalog emitters byte-for-byte against Python yaml.safe_dump. "
            "Do not hand-edit; regenerate."
        ),
        "cases": _cases(),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['cases'])} cases -> {_OUT}")


if __name__ == "__main__":
    main()
