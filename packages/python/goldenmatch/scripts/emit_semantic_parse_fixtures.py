"""Emit the cross-language fixture that locks the TS consume-side parsers
(`parseCubeModels` / `parseOsiModels` / `parseSemanticModels`) against Python.

The TS port extends the Cube/OSI parsers from join-only to the FULL model
(dimensions/measures/meta; datasets/fields/metrics/version/custom_extensions) and
adds `emitCubeYaml`/`emitOsiYaml`, so a whole existing dbt/Cube/OSI project can be
CONSUMED, not just have its keys certified. This records:

  - cube / osi: canonical Python-emitted YAML for full models. The TS test asserts
    `emit(parse(yaml)) === yaml` (a byte round-trip — any dropped/misread field
    breaks the re-emit), matching Python's own `parse(emit(...))` invariant.
  - metricflow: `parse_semantic_models` is a lossy key EXTRACTOR (not a full-model
    parser), so we record the parsed `DeclaredKeySpec`s and the TS parser must
    reproduce them.

These emit/parse functions depend only on `yaml` (+ `_load`), so we load the three
semantic modules in isolation rather than importing the whole toolkit.

Run: `python3 scripts/emit_semantic_parse_fixtures.py`
Output: packages/typescript/goldenmatch/tests/parity/fixtures/semantic/parse.json
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "goldenmatch"
_OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "semantic"
    / "parse.json"
)


def _load(name: str, relpath: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PKG / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:  # normal path (fully-installed goldenmatch)
    from goldenmatch.semantic import cube as cube_mod
    from goldenmatch.semantic import metricflow as mf_mod
    from goldenmatch.semantic import osi as osi_mod
except ImportError:  # only the "toolkit not installed" case -> isolated loader below

    sys.modules.setdefault("goldenmatch", types.ModuleType("goldenmatch"))
    sys.modules.setdefault("goldenmatch.semantic", types.ModuleType("goldenmatch.semantic"))
    mf_mod = _load("goldenmatch.semantic.metricflow", "semantic/metricflow.py")
    cube_mod = _load("goldenmatch.semantic.cube", "semantic/cube.py")
    osi_mod = _load("goldenmatch.semantic.osi", "semantic/osi.py")


def _cube_cases() -> list[dict]:
    Cube = cube_mod.Cube
    CubeDimension = cube_mod.CubeDimension
    CubeMeasure = cube_mod.CubeMeasure
    CubeJoin = cube_mod.CubeJoin
    emit_cube_yaml = cube_mod.emit_cube_yaml

    orders = Cube(
        name="orders",
        sql_table="public.orders",
        dimensions=[
            CubeDimension("id", "id", type="number", primary_key=True),
            CubeDimension("status", "status", type="string"),
            CubeDimension("created", "created_at", type="time"),
        ],
        measures=[
            CubeMeasure("count", type="count"),
            CubeMeasure("total", type="sum", sql="amount"),
        ],
        joins=[CubeJoin("customers", relationship="many_to_one", sql="{CUBE}.customer_id = {customers.id}")],
        meta={"goldenmatch": {"generated_by": "test", "tags": ["core", "orders"]}},
    )
    customers = Cube(
        name="customers",
        sql="SELECT * FROM crm.customers",
        dimensions=[CubeDimension("id", "id", type="number", primary_key=True)],
    )
    return [
        {"name": "cube_full_two_cubes", "yaml": emit_cube_yaml([orders, customers])},
        {"name": "cube_single_sql_table", "yaml": emit_cube_yaml(orders)},
    ]


def _osi_cases() -> list[dict]:
    OsiModel = osi_mod.OsiModel
    OsiDataset = osi_mod.OsiDataset
    OsiField = osi_mod.OsiField
    OsiRelationship = osi_mod.OsiRelationship
    OsiMetric = osi_mod.OsiMetric
    emit_osi_yaml = osi_mod.emit_osi_yaml

    model = OsiModel(
        name="sales",
        description="Sales semantic model",
        datasets=[
            OsiDataset(
                name="orders",
                source="public.orders",
                primary_key=["id"],
                unique_keys=[["order_no"]],
                fields=[
                    OsiField("id", "id", datatype="Integer"),
                    OsiField("created", "created_at", datatype="DateTime", is_time=True, label="Created At"),
                    OsiField("customer_id", "customer_id"),
                ],
            ),
            OsiDataset(
                name="customers",
                primary_key=["id"],
                fields=[OsiField("id", "id", datatype="Integer", description="Customer PK")],
            ),
        ],
        relationships=[
            OsiRelationship(
                "orders_to_customers",
                from_dataset="orders",
                to_dataset="customers",
                from_columns=["customer_id"],
                to_columns=["id"],
            )
        ],
        metrics=[OsiMetric("revenue", "SUM(orders.amount)", datatype="Decimal")],
        custom_extensions={"goldenmatch": {"generated_by": "test"}},
    )
    return [{"name": "osi_full_model", "yaml": emit_osi_yaml(model)}]


def _metricflow_cases() -> list[dict]:
    emit_semantic_model = mf_mod.emit_semantic_model
    emit_metricflow_yaml = mf_mod.emit_metricflow_yaml
    parse_semantic_models = mf_mod.parse_semantic_models

    sm = emit_semantic_model(
        "orders",
        resolved_key="resolved_entity_id",
        source_key="customer_id",
        measures=["revenue", "quantity"],
        grain="order_date",
    )
    yaml_str = emit_metricflow_yaml(sm)
    specs = parse_semantic_models(yaml_str)
    return [
        {
            "name": "metricflow_full",
            "yaml": yaml_str,
            "specs": [
                {
                    "model": s.model,
                    "key": list(s.key),
                    "measures": list(s.measures),
                    "grain": list(s.grain) if s.grain is not None else None,
                    "foreign_keys": list(s.foreign_keys),
                }
                for s in specs
            ],
        }
    ]


def main() -> None:
    payload = {
        "_comment": (
            "Generated by scripts/emit_semantic_parse_fixtures.py. Locks the TS "
            "consume-side parsers against Python. cube/osi: emit(parse(yaml))==yaml "
            "byte round-trip. metricflow: parse_semantic_models -> DeclaredKeySpec. "
            "Do not hand-edit; regenerate."
        ),
        "cube": _cube_cases(),
        "osi": _osi_cases(),
        "metricflow": _metricflow_cases(),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote cube={len(payload['cube'])} osi={len(payload['osi'])} metricflow={len(payload['metricflow'])} -> {_OUT}")


if __name__ == "__main__":
    main()
