"""Test the `goldenmatch certify-keys` CLI command (semantic-layer front door)."""
from __future__ import annotations

from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

_METRICFLOW = """
semantic_models:
  - name: orders
    model: ref('orders')
    entities:
      - name: order
        type: primary
        expr: order_id
"""


def test_certify_keys_reports_and_gates(tmp_path):
    model = tmp_path / "sm.yml"
    model.write_text(_METRICFLOW, encoding="utf-8")
    # order_id has a duplicate -> not unique at grain
    data = tmp_path / "orders.csv"
    data.write_text("order_id\n1\n1\n2\n", encoding="utf-8")

    # default: reports, exit 0
    res = runner.invoke(app, ["certify-keys", str(model), "-d", f"orders={data}"])
    assert res.exit_code == 0, res.output
    assert "metricflow" in res.output
    assert "order_id" in res.output

    # --fail-untrustworthy: the duplicate key trips the gate (exit 1)
    res2 = runner.invoke(
        app, ["certify-keys", str(model), "-d", f"orders={data}", "--fail-untrustworthy"]
    )
    assert res2.exit_code == 1, res2.output


def test_certify_keys_clean_key_passes_gate(tmp_path):
    model = tmp_path / "sm.yml"
    model.write_text(_METRICFLOW, encoding="utf-8")
    data = tmp_path / "orders.csv"
    data.write_text("order_id\n1\n2\n3\n", encoding="utf-8")
    res = runner.invoke(
        app, ["certify-keys", str(model), "-d", f"orders={data}", "--fail-untrustworthy"]
    )
    assert res.exit_code == 0, res.output


def test_certify_keys_bad_data_spec(tmp_path):
    model = tmp_path / "sm.yml"
    model.write_text(_METRICFLOW, encoding="utf-8")
    res = runner.invoke(app, ["certify-keys", str(model), "-d", "no-equals-sign"])
    assert res.exit_code == 2, res.output
