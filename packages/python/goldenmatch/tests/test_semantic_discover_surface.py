"""MCP + REST surface for semantic-model discovery (PR-6).

`discover_semantic_model` is surfaced on MCP (`discover_semantic_model` tool) and
REST (`POST /semantic/discover`), both returning the same `ProposedModel.to_dict()`
wire shape the `discover-model` CLI emits. These tests drive both entry points on
the canonical customers/orders fixture and cover the clean error paths (both
surfaces return `{"error": ...}` rather than raising).
"""
from __future__ import annotations

from pathlib import Path


def _write_fixture(tmp_path: Path) -> dict[str, str]:
    """customers (customer_id unique) + orders (order_id unique, customer_id FK,
    amount measure). Both grains are clean, so the draft is fully trustworthy."""
    customers = tmp_path / "customers.csv"
    customers.write_text(
        "customer_id,region\nc1,west\nc2,east\nc3,west\n", encoding="utf-8"
    )
    orders = tmp_path / "orders.csv"
    orders.write_text(
        "order_id,customer_id,amount\no1,c1,10.0\no2,c1,20.0\no3,c2,30.0\n",
        encoding="utf-8",
    )
    return {"customers": str(customers), "orders": str(orders)}


def test_mcp_discover_tool_registered():
    from goldenmatch.mcp.server import _BASE_TOOLS

    tool = next((t for t in _BASE_TOOLS if t.name == "discover_semantic_model"), None)
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert set(props) == {"frames", "dialect", "resolve"}
    assert tool.inputSchema["required"] == ["frames"]
    assert props["dialect"]["default"] == "metricflow"


def test_mcp_discover_tool_on_fixture(tmp_path: Path):
    from goldenmatch.mcp.server import _tool_discover_semantic_model

    frames = _write_fixture(tmp_path)
    out = _tool_discover_semantic_model(frames, "metricflow", False)

    assert "error" not in out
    assert out["dialect"] == "metricflow"
    assert out["all_trustworthy"] is True
    tables = {t["table"] for t in out["tables"]}
    assert tables == {"customers", "orders"}
    # A certified FK join from orders -> customers was proposed.
    assert any(
        j["from_table"] == "orders" and j["to_table"] == "customers"
        for j in out["joins"]
    )
    # The end-to-end certification report is attached.
    assert out["certification"].get("all_trustworthy") is True


def test_mcp_discover_tool_error_paths(tmp_path: Path):
    from goldenmatch.mcp.server import _tool_discover_semantic_model

    # Empty / non-dict frames.
    assert "error" in _tool_discover_semantic_model({}, "metricflow", False)
    # Missing data file.
    assert "error" in _tool_discover_semantic_model(
        {"orders": str(tmp_path / "nope.csv")}, "metricflow", False
    )
    # Unsupported dialect -> ValueError surfaced as a clean error.
    frames = _write_fixture(tmp_path)
    out = _tool_discover_semantic_model(frames, "no-such-dialect", False)
    assert "error" in out
    assert "no-such-dialect" in out["error"]


def test_rest_semantic_discover_endpoint(tmp_path: Path):
    from goldenmatch.api.server import _discover_semantic_model_endpoint

    frames = _write_fixture(tmp_path)
    out = _discover_semantic_model_endpoint(frames, "metricflow", False)

    assert "error" not in out
    assert out["all_trustworthy"] is True
    assert {t["table"] for t in out["tables"]} == {"customers", "orders"}

    # Error paths mirror the certify endpoint: clean {"error": ...}, never raise.
    assert "error" in _discover_semantic_model_endpoint({}, "metricflow", False)
    assert "error" in _discover_semantic_model_endpoint(
        {"orders": "/nope.csv"}, "metricflow", False
    )
    assert "error" in _discover_semantic_model_endpoint(
        frames, "no-such-dialect", False
    )


def test_mcp_and_rest_agree(tmp_path: Path):
    """One wire contract: the MCP tool and the REST endpoint return identical shapes."""
    from goldenmatch.api.server import _discover_semantic_model_endpoint
    from goldenmatch.mcp.server import _tool_discover_semantic_model

    frames = _write_fixture(tmp_path)
    mcp_out = _tool_discover_semantic_model(frames, "metricflow", False)
    rest_out = _discover_semantic_model_endpoint(frames, "metricflow", False)
    assert mcp_out == rest_out
