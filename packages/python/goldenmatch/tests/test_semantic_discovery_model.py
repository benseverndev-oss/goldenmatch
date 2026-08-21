"""Semantic-model discovery orchestrator (Phase 5).

`discover_semantic_model` assembles keys + entity types + joins + measures into a draft
MetricFlow model, emits it via the existing emitters, and re-certifies end-to-end. The
CLI `discover-model` is the front door. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.semantic import ProposedModel, discover_semantic_model


def _customers() -> pa.Table:
    return pa.table(
        {
            "customer_id": ["c1", "c2", "c3"],
            "name": ["Acme", "Globex", "Initech"],
            "region": ["west", "east", "west"],
        }
    )


def _orders() -> pa.Table:
    return pa.table(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c1", "c2", "c3"],
            "status": ["new", "new", "done", "new"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )


def _tables() -> dict[str, pa.Table]:
    return {"customers": _customers(), "orders": _orders()}


def test_orchestrator_assembles_all_phases() -> None:
    m = discover_semantic_model(_tables())
    assert isinstance(m, ProposedModel)
    assert m.dialect == "metricflow"
    assert {t.table for t in m.tables} == {"customers", "orders"}
    # certified FK graph is present
    fk = [
        j for j in m.joins
        if j.from_table == "orders" and j.from_column == "customer_id"
        and j.to_table == "customers"
    ]
    assert fk and fk[0].is_trustworthy


def test_emitted_yaml_round_trips_and_is_certified() -> None:
    m = discover_semantic_model(_tables())
    assert "semantic_models" in m.yaml
    # the emitted model round-trips through the parser
    from goldenmatch.semantic import parse_semantic_models

    specs = {s.model for s in parse_semantic_models(m.yaml)}
    assert {"customers", "orders"} <= specs
    # end-to-end certification ran and reports per-key verdicts
    assert m.certification.get("dialect") == "metricflow"
    assert m.certification.get("n_certified", 0) >= 2


def test_sum_safe_measure_only_on_clean_grain() -> None:
    m = discover_semantic_model(_tables())
    orders = [t for t in m.tables if t.table == "orders"][0]
    # orders' grain is the clean order_id, so amount is SUM-safe.
    amount = [x for x in orders.measures if x.column == "amount"][0]
    assert amount.safe_to_sum is True
    assert "amount" in m.yaml  # the safe measure is declared in the emitted model


def test_all_trustworthy_headline() -> None:
    m = discover_semantic_model(_tables())
    # both tables have a clean single-column key → the whole model certifies trustworthy.
    assert m.all_trustworthy is True
    d = m.to_dict()
    assert d["all_trustworthy"] is True
    assert d["tables"] and d["joins"]


def test_unsupported_dialect_raises() -> None:
    with pytest.raises(ValueError):
        discover_semantic_model(_tables(), dialect="looker")


def test_cli_discover_model(tmp_path) -> None:
    import csv

    from goldenmatch.cli.main import app
    from typer.testing import CliRunner

    # write the two tables as CSVs
    paths = {}
    for name, cols in {
        "customers": [
            ("customer_id", "name"), ("c1", "Acme"), ("c2", "Globex"), ("c3", "Initech"),
        ],
        "orders": [
            ("order_id", "customer_id", "amount"),
            ("o1", "c1", "10"), ("o2", "c1", "20"), ("o3", "c2", "30"), ("o4", "c3", "40"),
        ],
    }.items():
        p = tmp_path / f"{name}.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(cols)
        paths[name] = str(p)

    out = tmp_path / "model.yml"
    result = CliRunner().invoke(
        app,
        [
            "discover-model",
            "-d", f"customers={paths['customers']}",
            "-d", f"orders={paths['orders']}",
            "-o", str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "semantic_models" in out.read_text()
    import json

    payload = json.loads(result.output)
    assert payload["dialect"] == "metricflow"
    assert {t["table"] for t in payload["tables"]} == {"customers", "orders"}
