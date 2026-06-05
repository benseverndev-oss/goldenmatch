"""Tests for the A2A protocol server."""

from __future__ import annotations

import json

import pytest

try:
    import aiohttp  # noqa: F401  # availability check for optional dep
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")


# ── build_agent_card ─────────────────────────────────────────────────────────


def test_agent_card_has_required_fields():
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    assert card["name"]
    assert card["description"]
    assert card["url"] == "http://localhost:8080"
    assert card["version"]
    assert card["provider"]["organization"] == "GoldenMatch"
    assert card["provider"]["url"] == "https://github.com/benseverndev-oss/goldenmatch"
    # streaming is advertised as False until _handle_send_task actually streams
    # (Wave 1.4); advertising True while synchronous makes clients hang.
    assert card["capabilities"]["streaming"] is False
    assert card["capabilities"]["pushNotifications"] is False
    assert card["authentication"]["schemes"] == ["bearer"]


def test_agent_card_has_31_skills():
    """v1.7-v1.12 added autoconfig+controller_telemetry (10->12); v2.0 added
    six identity_* skills (12->18); v1.19.x Phase 3 added add_correction
    (18->19); the MCP tool-coverage parity pass added 12 (19->31)."""
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    assert len(card["skills"]) == 31
    ids = {s["id"] for s in card["skills"]}
    assert "autoconfig" in ids
    assert "controller_telemetry" in ids
    assert "add_correction" in ids
    assert {
        "identity_resolve", "identity_list", "identity_history",
        "identity_conflicts", "identity_merge", "identity_split",
    } <= ids
    # MCP tool-coverage parity pass.
    assert {
        "evaluate", "analyze_blocking", "compare_clusters", "schema_match",
        "sensitivity", "incremental", "identity_show", "list_runs", "rollback",
        "list_corrections", "learn_thresholds", "memory_stats",
    } <= ids


def test_agent_card_skills_have_modes():
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    for skill in card["skills"]:
        assert "id" in skill
        assert "name" in skill
        assert "description" in skill
        assert "inputModes" in skill and len(skill["inputModes"]) > 0
        assert "outputModes" in skill and len(skill["outputModes"]) > 0


def test_agent_card_valid_json():
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    # Round-trip through JSON to ensure serialisable
    text = json.dumps(card)
    parsed = json.loads(text)
    assert parsed["name"] == card["name"]


# ── TaskRegistry ─────────────────────────────────────────────────────────────


def test_registry_create_and_get_state():
    from goldenmatch.a2a.server import TaskRegistry

    reg = TaskRegistry()
    tid = reg.create_task("analyze_data", {"file_path": "test.csv"})
    assert reg.get_state(tid) == "submitted"


def test_registry_state_transitions():
    from goldenmatch.a2a.server import TaskRegistry

    reg = TaskRegistry()
    tid = reg.create_task("deduplicate", {})
    reg.set_state(tid, "working")
    assert reg.get_state(tid) == "working"
    reg.set_state(tid, "completed", result={"clusters": 5})
    assert reg.get_state(tid) == "completed"
    assert reg.get_result(tid) == {"clusters": 5}


def test_registry_cancel():
    from goldenmatch.a2a.server import TaskRegistry

    reg = TaskRegistry()
    tid = reg.create_task("match", {})
    reg.set_state(tid, "canceled")
    assert reg.get_state(tid) == "canceled"


def test_registry_unknown_task_raises():
    from goldenmatch.a2a.server import TaskRegistry

    reg = TaskRegistry()
    with pytest.raises(KeyError):
        reg.get_state("nonexistent-id")


def test_registry_list_tasks():
    from goldenmatch.a2a.server import TaskRegistry

    reg = TaskRegistry()
    tid1 = reg.create_task("analyze_data", {})
    tid2 = reg.create_task("deduplicate", {})
    tasks = reg.list_tasks()
    assert len(tasks) == 2
    ids = {t["id"] for t in tasks}
    assert tid1 in ids
    assert tid2 in ids


# ── dispatch_skill ───────────────────────────────────────────────────────────


def test_dispatch_analyze_data(tmp_path):
    import polars as pl
    from goldenmatch.a2a.skills import dispatch_skill

    csv_path = tmp_path / "data.csv"
    df = pl.DataFrame({
        "name": ["Alice", "Bob", "Charlie"],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "city": ["NYC", "LA", "NYC"],
    })
    df.write_csv(str(csv_path))

    result = dispatch_skill("analyze_data", {"file_path": str(csv_path)})
    assert "profile" in result
    assert "strategy" in result
    assert result["profile"]["row_count"] == 3


def test_dispatch_unknown_skill():
    from goldenmatch.a2a.skills import dispatch_skill

    with pytest.raises(ValueError, match="Unknown skill"):
        dispatch_skill("nonexistent_skill", {})


# ── MCP tool-coverage parity skills ───────────────────────────────────────────


def _exact_email_cfg(tmp_path) -> str:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "matchkeys:\n"
        "  - name: exact_email\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n"
        "        transforms: [lowercase, strip]\n"
    )
    return str(cfg)


def test_dispatch_compare_clusters(tmp_path):
    import json

    from goldenmatch.a2a.skills import dispatch_skill

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"0": {"members": [0, 1, 2]}}))
    b.write_text(json.dumps({"0": {"members": [0, 1]}, "1": {"members": [2]}}))
    result = dispatch_skill("compare_clusters", {
        "clusters_a_path": str(a), "clusters_b_path": str(b),
    })
    assert "twi" in result
    assert result["cc1"] == 1 and result["cc2"] == 2


def test_dispatch_schema_match(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    fa = tmp_path / "a.csv"
    fb = tmp_path / "b.csv"
    fa.write_text("full_name,email\nJohn,j@x.com\n")
    fb.write_text("contact_name,email_address\nJohn,j@x.com\n")
    result = dispatch_skill("schema_match", {"file_a": str(fa), "file_b": str(fb)})
    assert isinstance(result["mappings"], list)
    assert len(result["mappings"]) >= 1


def test_dispatch_list_runs_and_rollback(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    assert dispatch_skill("list_runs", {"output_dir": str(tmp_path)})["runs"] == []
    assert "error" in dispatch_skill("rollback", {"run_id": "nope", "output_dir": str(tmp_path)})


def test_dispatch_evaluate(tmp_path):
    import csv

    from goldenmatch.a2a.skills import dispatch_skill

    data = tmp_path / "data.csv"
    with open(data, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "email"])
        w.writerow(["John Smith", "john@x.com"])
        w.writerow(["Jon Smith", "john@x.com"])
    gt = tmp_path / "gt.csv"
    with open(gt, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["id_a", "id_b"])
        w.writerow([0, 1])
    result = dispatch_skill("evaluate", {
        "file_path": str(data), "config": _exact_email_cfg(tmp_path), "ground_truth": str(gt),
    })
    assert "f1" in result and "precision" in result


def test_dispatch_incremental(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    base = tmp_path / "base.csv"
    new = tmp_path / "new.csv"
    base.write_text("name,email\nJohn Smith,john@x.com\nJane Doe,jane@x.com\n")
    new.write_text("name,email\nJohnny Smith,john@x.com\nBob New,bob@x.com\n")
    result = dispatch_skill("incremental", {
        "base_file": str(base), "new_records": str(new), "config": _exact_email_cfg(tmp_path),
    })
    assert result["matched_to_base"] == 1
    assert result["new_entities"] == 1


def test_dispatch_analyze_blocking(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    data = tmp_path / "data.csv"
    data.write_text("name,email\nA,a@x.com\nB,b@x.com\n")
    result = dispatch_skill("analyze_blocking", {
        "file_path": str(data), "config": _exact_email_cfg(tmp_path),
    })
    assert "suggestions" in result
    assert isinstance(result["suggestions"], list)


def test_dispatch_sensitivity_requires_sweep(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    data = tmp_path / "data.csv"
    data.write_text("name,email\nA,a@x.com\n")
    result = dispatch_skill("sensitivity", {
        "file_path": str(data), "sweep": [], "config": _exact_email_cfg(tmp_path),
    })
    assert "error" in result


def test_dispatch_identity_show(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill
    from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id

    path = str(tmp_path / "identity.db")
    eid = new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=eid, dataset="d", confidence=0.9))
        s.upsert_record(SourceRecord("src:1", "src", "1", "h1", entity_id=eid, dataset="d"))
    result = dispatch_skill("identity_show", {"entity_id": eid, "path": path})
    assert result.get("entity_id") == eid


def test_dispatch_memory_loop(tmp_path):
    from goldenmatch.a2a.skills import dispatch_skill

    path = str(tmp_path / "memory.db")
    dispatch_skill("add_correction", {
        "decision": "reject", "id_a": 1, "id_b": 2, "dataset": "ds1", "path": path,
    })
    listed = dispatch_skill("list_corrections", {"path": path, "dataset": "ds1"})
    assert listed["count"] == 1
    stats = dispatch_skill("memory_stats", {"path": path})
    assert stats["total_corrections"] == 1


def test_agent_card_has_quality_and_transform_skills():
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    skill_ids = {s["id"] for s in card["skills"]}
    assert "quality" in skill_ids
    assert "transform" in skill_ids


def test_dispatch_quality_without_goldencheck(tmp_path):
    """quality skill returns error when goldencheck is not installed."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.a2a.skills import dispatch_skill

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice"]}).write_csv(str(csv_path))

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=False):
        result = dispatch_skill("quality", {"file_path": str(csv_path)})
    assert "error" in result
    assert "goldencheck" in result["error"].lower()


def test_dispatch_transform_without_goldenflow(tmp_path):
    """transform skill returns error when goldenflow is not installed."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.a2a.skills import dispatch_skill

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice"]}).write_csv(str(csv_path))

    with patch("goldenmatch.core.transform._goldenflow_available", return_value=False):
        result = dispatch_skill("transform", {"file_path": str(csv_path)})
    assert "error" in result
    assert "goldenflow" in result["error"].lower()


# ── MCP agent tools: quality & transforms ───────────────────────────────────


def test_mcp_scan_quality_tool_registered():
    """scan_quality tool is in the AGENT_TOOLS list."""
    from goldenmatch.mcp.agent_tools import AGENT_TOOLS

    names = {t.name for t in AGENT_TOOLS}
    assert "scan_quality" in names
    assert "fix_quality" in names
    assert "run_transforms" in names


def test_mcp_scan_quality_without_goldencheck(tmp_path):
    """scan_quality returns error when goldencheck is not installed."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice"]}).write_csv(str(csv_path))

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=False):
        result = handle_agent_tool("scan_quality", {"file_path": str(csv_path)})

    text = result[0].text
    parsed = json.loads(text)
    assert "error" in parsed
    assert "goldencheck" in parsed["error"].lower()


def test_mcp_fix_quality_without_goldencheck(tmp_path):
    """fix_quality returns error when goldencheck is not installed."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice"]}).write_csv(str(csv_path))

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=False):
        result = handle_agent_tool("fix_quality", {"file_path": str(csv_path)})

    text = result[0].text
    parsed = json.loads(text)
    assert "error" in parsed
    assert "goldencheck" in parsed["error"].lower()


def test_mcp_run_transforms_without_goldenflow(tmp_path):
    """run_transforms returns error when goldenflow is not installed."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice"]}).write_csv(str(csv_path))

    with patch("goldenmatch.core.transform._goldenflow_available", return_value=False):
        result = handle_agent_tool("run_transforms", {"file_path": str(csv_path)})

    text = result[0].text
    parsed = json.loads(text)
    assert "error" in parsed
    assert "goldenflow" in parsed["error"].lower()


# ── File validation ────────────────────────────────────────────────────────


def test_mcp_scan_quality_file_not_found():
    """scan_quality returns actionable error for missing file."""
    from unittest.mock import patch

    from goldenmatch.mcp.agent_tools import handle_agent_tool

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True):
        result = handle_agent_tool("scan_quality", {"file_path": "/nonexistent/data.csv"})

    parsed = json.loads(result[0].text)
    assert "error" in parsed
    assert "not found" in parsed["error"].lower() or "could not read" in parsed["error"].lower()


def test_mcp_scan_quality_missing_file_path():
    """scan_quality returns error when file_path is missing."""
    from unittest.mock import patch

    from goldenmatch.mcp.agent_tools import handle_agent_tool

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True):
        result = handle_agent_tool("scan_quality", {})

    parsed = json.loads(result[0].text)
    assert "error" in parsed
    assert "file_path" in parsed["error"].lower()


def test_a2a_quality_file_not_found():
    """A2A quality skill returns error for missing file."""
    from unittest.mock import patch

    from goldenmatch.a2a.skills import dispatch_skill

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True):
        result = dispatch_skill("quality", {"file_path": "/nonexistent/data.csv"})
    assert "error" in result
    assert "not found" in result["error"].lower() or "could not read" in result["error"].lower()


def test_a2a_quality_missing_file_path():
    """A2A quality skill returns error when file_path is missing."""
    from unittest.mock import patch

    from goldenmatch.a2a.skills import dispatch_skill

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True):
        result = dispatch_skill("quality", {})
    assert "error" in result
    assert "file_path" in result["error"].lower()


# ── Happy-path tests (mocked deps) ─────────────────────────────────────────


def test_mcp_scan_quality_happy_path(tmp_path):
    """scan_quality returns correct response shape when goldencheck works."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    pl.DataFrame({"name": ["Alice", "Bob"], "email": ["a@x.com", "b@x.com"]}).write_csv(str(csv_path))

    mock_issues = [
        {"rule": "ENC001", "severity": "warning", "column": "name",
         "message": "Mixed encoding", "rows_affected": 1, "confidence": 0.9},
    ]

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True), \
         patch("goldenmatch.core.quality.run_quality_check", return_value=(pl.DataFrame(), mock_issues)):
        result = handle_agent_tool("scan_quality", {"file_path": str(csv_path)})

    parsed = json.loads(result[0].text)
    assert "error" not in parsed
    assert parsed["total_records"] == 2
    assert parsed["issues_found"] == 1
    assert parsed["issues"] == mock_issues
    assert parsed["file"] == str(csv_path)


def test_mcp_fix_quality_happy_path(tmp_path):
    """fix_quality returns fixes and writes output file."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    out_path = tmp_path / "fixed.csv"
    df = pl.DataFrame({"name": ["Alice"], "email": ["a@x.com"]})
    df.write_csv(str(csv_path))

    mock_fixes = [{"fix": "goldencheck:encoding", "column": "name",
                   "rows_affected": 1, "detail": "encoding: 1 rows"}]

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True), \
         patch("goldenmatch.core.quality.run_quality_check", return_value=(df, mock_fixes)):
        result = handle_agent_tool("fix_quality", {
            "file_path": str(csv_path), "fix_mode": "moderate",
            "output_path": str(out_path),
        })

    parsed = json.loads(result[0].text)
    assert "error" not in parsed
    assert parsed["fix_mode"] == "moderate"
    assert parsed["fixes_applied"] == 1
    assert parsed["output_path"] == str(out_path)
    assert out_path.exists()


def test_mcp_run_transforms_happy_path(tmp_path):
    """run_transforms returns transforms and writes output file."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    out_path = tmp_path / "transformed.csv"
    df = pl.DataFrame({"phone": ["5551234567"]})
    df.write_csv(str(csv_path))

    mock_fixes = [{"fix": "goldenflow:phone_e164", "column": "phone",
                   "rows_affected": 1, "detail": "phone_e164: 1 rows"}]

    with patch("goldenmatch.core.transform._goldenflow_available", return_value=True), \
         patch("goldenmatch.core.transform.run_transform", return_value=(df, mock_fixes)):
        result = handle_agent_tool("run_transforms", {
            "file_path": str(csv_path), "output_path": str(out_path),
        })

    parsed = json.loads(result[0].text)
    assert "error" not in parsed
    assert parsed["transforms_applied"] == 1
    assert parsed["output_path"] == str(out_path)
    assert out_path.exists()


def test_a2a_quality_happy_path(tmp_path):
    """A2A quality skill returns correct response with fixes."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.a2a.skills import dispatch_skill

    csv_path = tmp_path / "data.csv"
    df = pl.DataFrame({"name": ["Alice"]})
    df.write_csv(str(csv_path))

    mock_fixes = [{"fix": "goldencheck:unicode", "column": "name",
                   "rows_affected": 1, "detail": "unicode: 1 rows"}]

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True), \
         patch("goldenmatch.core.quality.run_quality_check", return_value=(df, mock_fixes)):
        result = dispatch_skill("quality", {"file_path": str(csv_path)})

    assert "error" not in result
    assert result["fixes_applied"] == 1
    assert result["total_records"] == 1


def test_a2a_transform_happy_path(tmp_path):
    """A2A transform skill returns correct response."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.a2a.skills import dispatch_skill

    csv_path = tmp_path / "data.csv"
    df = pl.DataFrame({"date": ["01/15/2024"]})
    df.write_csv(str(csv_path))

    mock_fixes = [{"fix": "goldenflow:date_iso", "column": "date",
                   "rows_affected": 1, "detail": "date_iso: 1 rows"}]

    with patch("goldenmatch.core.transform._goldenflow_available", return_value=True), \
         patch("goldenmatch.core.transform.run_transform", return_value=(df, mock_fixes)):
        result = dispatch_skill("transform", {"file_path": str(csv_path)})

    assert "error" not in result
    assert result["transforms_applied"] == 1


# ── Output write failure ───────────────────────────────────────────────────


def test_mcp_fix_quality_write_failure_preserves_results(tmp_path):
    """fix_quality preserves results when output write fails."""
    from unittest.mock import patch

    import polars as pl
    from goldenmatch.mcp.agent_tools import handle_agent_tool

    csv_path = tmp_path / "data.csv"
    df = pl.DataFrame({"name": ["Alice"]})
    df.write_csv(str(csv_path))

    mock_fixes = [{"fix": "goldencheck:encoding", "column": "name",
                   "rows_affected": 1, "detail": "test"}]

    with patch("goldenmatch.core.quality._goldencheck_available", return_value=True), \
         patch("goldenmatch.core.quality.run_quality_check", return_value=(df, mock_fixes)):
        result = handle_agent_tool("fix_quality", {
            "file_path": str(csv_path),
            "output_path": "/nonexistent/dir/out.csv",
        })

    parsed = json.loads(result[0].text)
    assert parsed["fixes_applied"] == 1
    assert "write_error" in parsed
    assert parsed["output_path"] is None
