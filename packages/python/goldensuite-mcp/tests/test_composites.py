from goldensuite_mcp.composites import run_step


def _table(**tools):
    return dict(tools)


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
    import os
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    tools, dispatch = _aggregate()
    names = {t.name for t in tools}
    assert "dedupe_file" in names
    assert "dedupe_file" in dispatch
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert "dedupe_file" in listed
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    assert "dedupe_file" in {r["name"] for r in out["tools"]}
