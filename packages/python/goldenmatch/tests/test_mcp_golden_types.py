"""The golden-reading MCP tools must handle BOTH golden frame types.

Standalone `MatchEngine.run_full` yields `EngineResult.golden: pl.DataFrame`,
while session/`dedupe_df` runs yield `DedupeResult.golden: pa.Table` (post the
W5 Arrow flip). `_tool_export_results` / `_tool_get_golden_record` are written
against the pa.Table API, so a polars golden used to `AttributeError`
(`'DataFrame' object has no attribute 'to_pylist'`). `_golden_as_table`
normalizes; these tests lock both types.
"""
import polars as pl
import pyarrow as pa


def test_golden_as_table_normalizes_polars_and_passes_arrow():
    from goldenmatch.mcp.server import _golden_as_table

    assert _golden_as_table(None) is None
    tbl = pa.table({"a": [1, 2]})
    assert _golden_as_table(tbl) is tbl  # pa.Table passthrough (identity)
    df = pl.DataFrame({"a": [1, 2]})
    out = _golden_as_table(df)
    assert isinstance(out, pa.Table) and out.num_rows == 2


def _run(monkeypatch, golden):
    from goldenmatch.mcp import server as gm
    fake = type("R", (), {"golden": golden})()
    monkeypatch.setattr(gm, "_result", fake)
    return gm


def test_export_and_golden_record_handle_polars_golden(tmp_path, monkeypatch):
    """The exact pre-existing bug: standalone polars golden -> the tools crashed."""
    golden = pl.DataFrame({"__cluster_id__": [0, 1], "name": ["A", "B"]})
    gm = _run(monkeypatch, golden)

    out_json = tmp_path / "g.json"
    r = gm._tool_export_results(str(out_json), "json")
    assert "error" not in r and out_json.exists() and r["records"] == 2
    # internal __ columns stripped in the written JSON
    assert "__cluster_id__" not in out_json.read_text()

    out_csv = tmp_path / "g.csv"
    r2 = gm._tool_export_results(str(out_csv), "csv")
    assert "error" not in r2 and out_csv.exists()

    r3 = gm._tool_get_golden_record(0)
    assert "error" not in r3 and r3["golden_record"]["name"] == "A"


def test_export_handles_arrow_golden_unchanged(tmp_path, monkeypatch):
    """The session pa.Table path keeps working (regression guard)."""
    golden = pa.table({"__cluster_id__": [0], "name": ["A"]})
    gm = _run(monkeypatch, golden)
    out = tmp_path / "g.csv"
    r = gm._tool_export_results(str(out), "csv")
    assert "error" not in r and out.exists() and r["records"] == 1
