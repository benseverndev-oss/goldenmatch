import csv as _csv
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from goldensuite_mcp.composites import run_step


def _table(**tools):
    return dict(tools)


def _pipe_available():
    import importlib.util
    return importlib.util.find_spec("goldenpipe") is not None


def test_run_step_success():
    t = _table(foo=lambda n, a: {"value": a["x"] + 1})
    ok, res = run_step(t, "foo", {"x": 1})
    assert ok is True and res == {"value": 2}


def test_run_step_error_dict_is_failure():
    t = _table(foo=lambda n, a: {"error": "boom"})
    ok, res = run_step(t, "foo", {})
    assert ok is False and "boom" in res["error"]


def test_run_step_raise_is_failure():
    def boom(n, a):
        raise ValueError("kaboom")

    ok, res = run_step(_table(foo=boom), "foo", {})
    assert ok is False and "kaboom" in res["error"]


def test_run_step_missing_tool_is_failure():
    ok, res = run_step({}, "nope", {})
    assert ok is False and "nope" in res["error"]


def _fake_dedupe_table(rec):
    def upload(n, a):
        rec.append(("upload", a))
        return {"path": "/up/in.csv", "bytes": 10, "filename": "in.csv"}

    def autoconf(n, a):
        rec.append(("autoconf", a))
        return {"config": {"matchkeys": ["exact(email)"]}}

    def dedup(n, a):
        rec.append(("dedup", a))
        # Contract: dedupe_file must NOT hand auto_configure's display dict to
        # agent_deduplicate -- its `config` param wants a Config object/None.
        assert "config" not in a, "dedupe_file must not pass config to agent_deduplicate"
        return {"confidence_distribution": {"auto_merged": 2, "review": 1, "auto_rejected": 0},
                "golden_path": a["output_path"], "golden_records": 3, "results": {"total_records": 4}}

    return {"upload_dataset": upload, "auto_configure": autoconf, "agent_deduplicate": dedup}


def test_dedupe_file_shape_and_threading():
    from goldensuite_mcp.composites import build_composites
    rec = []
    tools, dispatch = build_composites(_fake_dedupe_table(rec))
    out = dispatch["dedupe_file"]("dedupe_file", {"file_content": "...", "filename": "in.csv"})
    assert out["workflow"] == "dedupe_file"
    assert out["ok"] is True
    steps = [s["step"] for s in out["steps"]]
    assert steps == ["upload", "auto_configure", "deduplicate"]
    assert dict(rec)["autoconf"]["file_path"] == "/up/in.csv"
    assert dict(rec)["dedup"]["file_path"] == "/up/in.csv"
    assert out["outputs"]["golden_path"].endswith(".golden.csv")
    assert isinstance(out["summary"], str) and out["summary"]


def test_dedupe_file_short_circuits_on_step_failure():
    from goldensuite_mcp.composites import build_composites
    tbl = _fake_dedupe_table([])
    tbl["auto_configure"] = lambda n, a: {"error": "autoconf boom"}
    _, dispatch = build_composites(tbl)
    out = dispatch["dedupe_file"]("dedupe_file", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload", "auto_configure"]
    assert "boom" in out["summary"].lower() or "auto_configure" in out["summary"]


def test_dedupe_file_registered_and_curated():
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    tools, dispatch = _aggregate()
    names = {t.name for t in tools}
    assert "dedupe_file" in names
    assert "dedupe_file" in dispatch
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert "dedupe_file" in listed
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    assert "dedupe_file" in {r["name"] for r in out["tools"]}


# --- Task 2.4: assess_file (read-only, degraded-aware) --------------------

def _fake_assess_table(rec, with_scan=True):
    def upload(n, a):
        rec.append(("upload", a))
        return {"path": "/up/in.csv", "bytes": 10, "filename": "in.csv"}

    def analyze(n, a):
        rec.append(("analyze", a))
        return {"total_records": 100, "domain": "healthcare", "recommended_strategy": "exact(email)"}

    def scan(n, a):
        rec.append(("scan", a))
        return {"rows": 100, "columns": 4, "health_grade": "B", "health_score": 82,
                "total_findings": 3, "errors": 1, "warnings": 2}

    t = {"upload_dataset": upload, "analyze_data": analyze}
    if with_scan:
        t["scan"] = scan
    return t


def test_assess_file_happy():
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_assess_table(rec))
    out = dispatch["assess_file"]("assess_file", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is True
    assert [s["step"] for s in out["steps"]] == ["upload", "analyze", "scan"]
    assert dict(rec)["analyze"]["file_path"] == "/up/in.csv"
    assert dict(rec)["scan"]["file_path"] == "/up/in.csv"
    # read-only: no config, no outputs
    assert "config" not in out and "outputs" not in out
    # summary mentions rows + a quality signal
    assert "100" in out["summary"]
    assert "B" in out["summary"] or "finding" in out["summary"].lower()


def test_assess_file_degraded_without_scan():
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_assess_table(rec, with_scan=False))
    out = dispatch["assess_file"]("assess_file", {"file_content": "...", "filename": "in.csv"})
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["analyze"]["ok"] is True
    assert steps["scan"]["ok"] is False
    # degraded but successful: a missing optional scan does not fail the composite
    assert out["ok"] is True
    assert "profile" in steps["analyze"]  # profile still present
    assert not out["summary"].startswith("assess_file failed at step")


# --- Task 2.5: match_sources ----------------------------------------------

def _fake_match_table(rec):
    def upload(n, a):
        rec.append(("upload", a))
        return {"path": f"/up/{a.get('filename', 'x')}", "bytes": 10, "filename": a.get("filename")}

    def match(n, a):
        rec.append(("match", a))
        return {"matches_path": a["output_path"], "matched_pairs": 7,
                "results": {"match_rate": 0.42, "total_matched_records": 14}}

    return {"upload_dataset": upload, "agent_match_sources": match}


def test_match_sources_shape_and_threading():
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_match_table(rec))
    out = dispatch["match_sources"]("match_sources",
        {"file_a_content": "a", "file_a_name": "a.csv", "file_b_content": "b", "file_b_name": "b.csv"})
    assert out["ok"] is True
    assert [s["step"] for s in out["steps"]] == ["upload_a", "upload_b", "match"]
    # both files uploaded
    assert sum(1 for k, _ in rec if k == "upload") == 2
    m = dict(rec)["match"]
    assert m["file_a"] == "/up/a.csv"
    assert m["file_b"] == "/up/b.csv"
    assert m["output_path"].endswith(".matches.csv")
    assert out["outputs"]["matches_path"].endswith(".matches.csv")
    assert out["outputs"]["matched_pairs"] == 7
    assert isinstance(out["summary"], str) and out["summary"]


def test_match_sources_short_circuits_on_second_upload():
    from goldensuite_mcp.composites import build_composites
    tbl = _fake_match_table([])
    calls = {"n": 0}

    def upload(n, a):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"error": "upload b boom"}
        return {"path": f"/up/{a.get('filename', 'x')}", "filename": a.get("filename")}

    tbl["upload_dataset"] = upload
    _, dispatch = build_composites(tbl)
    out = dispatch["match_sources"]("match_sources",
        {"file_a_content": "a", "file_a_name": "a.csv", "file_b_content": "b", "file_b_name": "b.csv"})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload_a", "upload_b"]


# --- Task 2.6: clean_and_dedupe -------------------------------------------

def _fake_clean_table(rec, transforms_ok=True):
    def upload(n, a):
        rec.append(("upload", a))
        return {"path": "/up/in.csv", "bytes": 10, "filename": "in.csv"}

    def transforms(n, a):
        rec.append(("clean", a))
        if not transforms_ok:
            return {"error": "goldenflow is not installed. Install with: pip install goldenmatch[transform]"}
        return {"file": a["file_path"], "output_path": a["output_path"],
                "transforms_applied": 5, "total_records": 10}

    def dedup(n, a):
        rec.append(("dedup", a))
        return {"confidence_distribution": {"auto_merged": 1, "review": 0, "auto_rejected": 0},
                "golden_path": a["output_path"], "golden_records": 9, "results": {"total_records": 10}}

    return {"upload_dataset": upload, "run_transforms": transforms, "agent_deduplicate": dedup}


@patch("goldensuite_mcp.composites.HAS_PIPE", False)
def test_clean_and_dedupe_happy_fallback_chain():
    # Fallback (no goldenpipe): the legacy run_transforms -> agent_deduplicate
    # CSV chain still works and threads the cleaned path into the dedupe.
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_clean_table(rec))
    out = dispatch["clean_and_dedupe"]("clean_and_dedupe", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is True
    assert [s["step"] for s in out["steps"]] == ["upload", "clean", "deduplicate"]
    clean_call = dict(rec)["clean"]
    assert clean_call["file_path"] == "/up/in.csv"
    assert clean_call["output_path"].endswith(".cleaned.csv")
    dedup_call = dict(rec)["dedup"]
    # the cleaned path returned by run_transforms is threaded as the dedupe input
    assert dedup_call["file_path"].endswith(".cleaned.csv")
    assert out["outputs"]["golden_path"].endswith(".golden.csv")
    assert isinstance(out["summary"], str) and out["summary"]


@patch("goldensuite_mcp.composites.HAS_PIPE", False)
def test_clean_and_dedupe_soft_dep_short_circuit():
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_clean_table(rec, transforms_ok=False))
    out = dispatch["clean_and_dedupe"]("clean_and_dedupe", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload", "clean"]
    # deduplicate never ran
    assert not any(k == "dedup" for k, _ in rec)


def _fake_pipe_result(golden_rows=2, total=5, pairs=((0, 1, 1.0), (2, 3, 1.0))):
    import polars as pl
    golden = pl.DataFrame({"id": [str(i) for i in range(golden_rows)]})
    return SimpleNamespace(
        status=SimpleNamespace(value="success"),
        errors=[],
        artifacts={"golden": golden, "match_stats": {"total_records": total},
                   "scored_pairs": list(pairs)},
    )


def test_clean_and_dedupe_inprocess_single_step(tmp_path):
    # In-process path: one "pipeline" step, golden written once, NO cleaned.csv,
    # confidence buckets reconstructed from scored_pairs. run_transforms and
    # agent_deduplicate are NEVER dispatched.
    from goldensuite_mcp.composites import build_composites
    src = tmp_path / "in.csv"
    src.write_text("id\n1\n2\n")
    rec = []
    fake = _fake_clean_table(rec)  # dispatch fakes that must NOT be called
    fake_gp = SimpleNamespace(run=lambda p: _fake_pipe_result())
    with patch("goldensuite_mcp.composites.HAS_PIPE", True), \
            patch("goldensuite_mcp.composites._gp", fake_gp):
        _, dispatch = build_composites(fake)
        out = dispatch["clean_and_dedupe"]("clean_and_dedupe", {"file_path": str(src)})
    assert out["ok"] is True
    assert [s["step"] for s in out["steps"]] == ["upload", "pipeline"]
    assert not any(k in ("clean", "dedup") for k, _ in rec)  # legacy tools untouched
    assert out["outputs"]["golden_records"] == 2
    assert out["outputs"]["total_records"] == 5
    assert "cleaned_path" not in out["outputs"]  # no intermediate CSV
    gp = out["outputs"]["golden_path"]
    assert os.path.exists(gp)
    assert not os.path.exists(str(src).replace(".csv", ".cleaned.csv"))
    pipe_step = next(s for s in out["steps"] if s["step"] == "pipeline")
    assert pipe_step["auto_merge"] == 2 and pipe_step["review"] == 0  # gate_pairs buckets


def test_clean_and_dedupe_inprocess_pipeline_failure(tmp_path):
    from goldensuite_mcp.composites import build_composites
    src = tmp_path / "in.csv"
    src.write_text("id\n1\n")
    failed = SimpleNamespace(status=SimpleNamespace(value="failed"), errors=["boom"], artifacts={})
    fake_gp = SimpleNamespace(run=lambda p: failed)
    with patch("goldensuite_mcp.composites.HAS_PIPE", True), \
            patch("goldensuite_mcp.composites._gp", fake_gp):
        _, dispatch = build_composites({})
        out = dispatch["clean_and_dedupe"]("clean_and_dedupe", {"file_path": str(src)})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload", "pipeline"]
    assert "boom" in out["summary"]


@pytest.mark.skipif(not _pipe_available(), reason="goldenmatch autoconfig not available")
def test_clean_and_dedupe_inprocess_threads_exclude_columns(tmp_path):
    # exclude_columns must reach goldenmatch's auto-config via the same
    # _RUNTIME_EXCLUDE_COLUMNS ContextVar agent_deduplicate uses.
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    from goldensuite_mcp.composites import build_composites
    src = tmp_path / "in.csv"
    src.write_text("id\n1\n")
    seen = {}

    def _run(p):
        seen["excl"] = _RUNTIME_EXCLUDE_COLUMNS.get()  # captured mid-run
        return _fake_pipe_result()

    fake_gp = SimpleNamespace(run=_run)
    with patch("goldensuite_mcp.composites.HAS_PIPE", True), \
            patch("goldensuite_mcp.composites._gp", fake_gp):
        _, dispatch = build_composites({})
        dispatch["clean_and_dedupe"]("clean_and_dedupe",
                                     {"file_path": str(src), "exclude_columns": ["ssn"]})
    assert seen["excl"] == ["ssn"]
    # and it's reset afterward (no leak into the next call)
    assert _RUNTIME_EXCLUDE_COLUMNS.get() != ["ssn"]


@pytest.mark.skipif(not _pipe_available(), reason="goldenpipe not installed")
def test_clean_and_dedupe_inprocess_end_to_end(tmp_path):
    """Real GoldenPipe run through the composite: golden CSV lands, no cleaned.csv."""
    from goldensuite_mcp.composites import orchestrate_clean_and_dedupe
    csv_path = tmp_path / "people.csv"
    _write_people_csv(csv_path)
    # _upload short-circuits on a file_path arg, so an empty dispatch is fine.
    out = orchestrate_clean_and_dedupe({}, {"file_path": str(csv_path)})
    assert out["ok"] is True, out
    assert [s["step"] for s in out["steps"]] == ["upload", "pipeline"]
    golden = out["outputs"]["golden_path"]
    assert golden and os.path.exists(golden)
    assert not os.path.exists(str(csv_path).replace(".csv", ".cleaned.csv"))
    with open(golden, encoding="utf-8") as fh:
        rows = sum(1 for _ in fh)
    assert rows >= 2  # header + >=1 golden record


def test_all_four_composites_exist_no_dangling_curated():
    from goldensuite_mcp.server import CURATED_TOOLS, _aggregate
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    tools, _ = _aggregate()
    names = {t.name for t in tools}
    for c in ("dedupe_file", "assess_file", "match_sources", "clean_and_dedupe"):
        assert c in names, f"{c} not registered"
    # every composite curated name now resolves to a real tool
    assert {"dedupe_file", "assess_file", "match_sources", "clean_and_dedupe"} <= (CURATED_TOOLS & names)


# --- Task 2.7: end-to-end through the REAL aggregator ---------------------
#
# These run against _aggregate() (no fakes), on tiny CSVs written to a temp
# dir. assess_file needs no file-writing capability and runs anywhere the
# goldenmatch/goldencheck sub-packages import. dedupe_file needs Phase 1's
# agent_deduplicate `output_path`; it is skipped where that capability is
# absent (this worktree) and runs in CI once Phase 1 merges.


def _write_people_csv(path):
    rows = [
        {"id": "1", "name": "Alice Smith", "email": "alice@example.com"},
        {"id": "2", "name": "Alice Smith", "email": "alice@example.com"},
        {"id": "3", "name": "Bob Jones", "email": "bob@example.com"},
        {"id": "4", "name": "Bob Jones", "email": "bob@example.com"},
        {"id": "5", "name": "Carol White", "email": "carol@example.com"},
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=["id", "name", "email"])
        w.writeheader()
        w.writerows(rows)


def _real_aggregate():
    from goldensuite_mcp.server import _aggregate
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    return _aggregate()


def _has_output_path(tools, tool_name):
    t = next((t for t in tools if t.name == tool_name), None)
    return t is not None and "output_path" in (t.inputSchema.get("properties") or {})


def test_assess_file_end_to_end(tmp_path):
    """assess_file through the real aggregator on a real CSV (no writes)."""
    tools, dispatch = _real_aggregate()
    if "analyze_data" not in dispatch:
        pytest.skip("goldenmatch analyze_data not available in this build")
    csv_path = tmp_path / "people.csv"
    _write_people_csv(csv_path)

    os.environ["GOLDENMATCH_ALLOWED_ROOT"] = str(tmp_path)
    try:
        out = dispatch["assess_file"]("assess_file", {"file_path": str(csv_path)})
    finally:
        os.environ.pop("GOLDENMATCH_ALLOWED_ROOT", None)

    assert out["workflow"] == "assess_file"
    assert out["ok"] is True
    labels = [s["step"] for s in out["steps"]]
    assert labels[:2] == ["upload", "analyze"]
    analyze = next(s for s in out["steps"] if s["step"] == "analyze")
    assert analyze["ok"] is True and "profile" in analyze
    # read-only: nothing written
    assert "outputs" not in out


@pytest.mark.skipif(
    not _has_output_path(_real_aggregate()[0], "agent_deduplicate"),
    reason="agent_deduplicate lacks output_path (pre-Phase-1 goldenmatch build)",
)
def test_dedupe_file_end_to_end_and_parity(tmp_path):
    """dedupe_file end-to-end: golden CSV lands on disk, and its row count
    matches a by-hand upload+auto_configure+agent_deduplicate run."""
    tools, dispatch = _real_aggregate()
    csv_path = tmp_path / "people.csv"
    _write_people_csv(csv_path)
    os.environ["GOLDENMATCH_ALLOWED_ROOT"] = str(tmp_path)
    try:
        out = dispatch["dedupe_file"]("dedupe_file", {"file_path": str(csv_path)})
        assert out["ok"] is True, out
        golden = (out.get("outputs") or {}).get("golden_path")
        assert golden and os.path.exists(golden), f"golden not written: {golden}"
        with open(golden, encoding="utf-8") as fh:
            composite_rows = sum(1 for _ in fh)

        # By-hand parity path through the same aggregated dispatch. A file_path
        # input needs no upload (upload_dataset is for inline file_content), so
        # this mirrors what the composite does: auto_configure then dedupe.
        dispatch["auto_configure"]("auto_configure", {"file_path": str(csv_path)})
        manual_golden = str(tmp_path / "manual.golden.csv")
        dispatch["agent_deduplicate"](
            "agent_deduplicate", {"file_path": str(csv_path), "output_path": manual_golden})
        with open(manual_golden, encoding="utf-8") as fh:
            manual_rows = sum(1 for _ in fh)
    finally:
        os.environ.pop("GOLDENMATCH_ALLOWED_ROOT", None)

    assert composite_rows == manual_rows, (
        f"composite golden {composite_rows} rows != manual {manual_rows}")
