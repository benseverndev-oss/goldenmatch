"""Emit the cross-language fixture that locks the TS metric-aware roles reader
(`semanticFieldRoles` / `metricAwareAttributes`) against Python `semantic/blocking.py`.

The resolution tier's metric-aware attribute selection (the differentiated wedge)
is DETERMINISTIC — it reads a semantic model's declared roles ({keys, dimensions,
measures}) and turns them into the ER attribute allow-list, with no dependency on
the ER engine. So unlike the resolution tier itself (behavioral parity only), this
IS a byte-parity surface: the TS `semanticFieldRoles(doc)` must reproduce Python's
roles, and `metricAwareAttributes(roles, columns)` its allow-list, per dialect.

Records, per case:
  - `roles`: the `{keys, dimensions, measures}` Python `semantic_field_roles` reads.
  - `attribute_selection`: for each frame-column list, the
    `metric_aware_attributes(roles, columns)` output (declared dimensions present,
    never a key/measure; blind fallback when a model declares no dimensions).

`semantic/blocking.py` (and its dialect readers) depend only on `yaml` + `pyarrow`
(via the key_integrity Arrow adapter), so we load the semantic modules in isolation
rather than importing the whole toolkit (whose `__init__` pulls the heavy pipeline).

Run: `python3 scripts/emit_semantic_roles_fixtures.py`
Output: packages/typescript/goldenmatch/tests/parity/fixtures/semantic/roles.json
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
    / "roles.json"
)


def _load(name: str, relpath: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PKG / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:  # normal path (fully-installed goldenmatch)
    from goldenmatch.semantic.blocking import metric_aware_attributes, semantic_field_roles
except ImportError:  # toolkit not installed -> isolated loader (dependency order)
    sys.modules.setdefault("goldenmatch", types.ModuleType("goldenmatch"))
    sys.modules.setdefault("goldenmatch.semantic", types.ModuleType("goldenmatch.semantic"))
    sys.modules.setdefault("goldenmatch.core", types.ModuleType("goldenmatch.core"))
    _load("goldenmatch.core.key_integrity_certificate", "core/key_integrity_certificate.py")
    _load("goldenmatch.semantic.metricflow", "semantic/metricflow.py")
    _load("goldenmatch.semantic.cube", "semantic/cube.py")
    _load("goldenmatch.semantic.osi", "semantic/osi.py")
    _load("goldenmatch.semantic.key_integrity", "semantic/key_integrity.py")
    # feast is imported unconditionally by certify.py and (lazily) by blocking's
    # semantic_field_roles; load it so both resolve in this isolated bootstrap.
    _load("goldenmatch.semantic.feast", "semantic/feast.py")
    _load("goldenmatch.semantic.certify", "semantic/certify.py")
    _blocking = _load("goldenmatch.semantic.blocking", "semantic/blocking.py")
    metric_aware_attributes = _blocking.metric_aware_attributes
    semantic_field_roles = _blocking.semantic_field_roles


# --- model docs (mirror the TS unit-test shapes so the oracle is comparable) ---

_METRICFLOW = {
    "semantic_models": [
        {
            "name": "orders",
            "entities": [{"name": "orders", "type": "primary", "expr": "resolved_entity_id"}],
            "dimensions": [{"name": "email", "type": "categorical"}, {"name": "city"}],
            "measures": [{"name": "revenue", "agg": "sum", "expr": "revenue"}],
        }
    ]
}

_METRICFLOW_MULTI = {
    "semantic_models": [
        {"name": "a", "dimensions": [{"name": "email"}, {"name": "email"}, {"name": "city"}]},
        {"name": "b", "dimensions": [{"name": "city"}]},
    ]
}

_CUBE = {
    "cubes": [
        {
            "name": "orders",
            "sql_table": "public.orders",
            "dimensions": [
                {"name": "id", "sql": "id", "primary_key": True},
                {"name": "status", "sql": "status"},
            ],
            "measures": [{"name": "count", "type": "count"}],
        }
    ]
}

_OSI = {
    "version": "0.1",
    "semantic_model": [
        {
            "name": "sales",
            "datasets": [
                {
                    "name": "orders",
                    "primary_key": ["id"],
                    "fields": [{"name": "id"}, {"name": "email"}, {"name": "city"}],
                }
            ],
            "metrics": [{"name": "revenue", "expression": "SUM(orders.amount)"}],
        }
    ],
}

# Frame-column lists to exercise metric_aware_attributes per case (declared-dims
# present in frame order, key/measure exclusion, blind fallback, dims-absent).
_SELECTION_COLUMNS = {
    "metricflow_basic": [
        ["resolved_entity_id", "revenue", "city", "email", "notes"],
        ["resolved_entity_id", "revenue", "name"],  # no declared dim present -> blind
    ],
    "metricflow_multi_model": [
        ["email", "city", "phone"],
        ["a_id", "phone"],  # no declared dim present -> blind (nothing excluded)
    ],
    "cube_basic": [
        ["id", "status", "count", "region"],
        ["id", "count", "region"],  # status absent -> blind
    ],
    "osi_basic": [
        ["id", "email", "city", "revenue"],
        ["id", "revenue", "name"],  # email/city absent -> blind
    ],
}

_CASES = [
    ("metricflow_basic", _METRICFLOW),
    ("metricflow_multi_model", _METRICFLOW_MULTI),
    ("cube_basic", _CUBE),
    ("osi_basic", _OSI),
]


def _case(name: str, model: dict) -> dict:
    roles = semantic_field_roles(model)
    selection = [
        {"columns": cols, "attributes": metric_aware_attributes(roles, cols)}
        for cols in _SELECTION_COLUMNS[name]
    ]
    return {
        "name": name,
        "model": model,
        "roles": {"keys": roles.keys, "dimensions": roles.dimensions, "measures": roles.measures},
        "attribute_selection": selection,
    }


def main() -> None:
    payload = {
        "_comment": (
            "Generated by scripts/emit_semantic_roles_fixtures.py. Locks the TS "
            "metric-aware roles reader (semanticFieldRoles / metricAwareAttributes) "
            "against Python semantic/blocking.py, per dialect. Deterministic (no ER "
            "engine). Do not hand-edit; regenerate."
        ),
        "cases": [_case(name, model) for name, model in _CASES],
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['cases'])} cases -> {_OUT}")


if __name__ == "__main__":
    main()
