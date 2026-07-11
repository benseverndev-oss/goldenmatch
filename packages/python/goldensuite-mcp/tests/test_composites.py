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


def test_clean_and_dedupe_happy():
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


def test_clean_and_dedupe_soft_dep_short_circuit():
    from goldensuite_mcp.composites import build_composites
    rec = []
    _, dispatch = build_composites(_fake_clean_table(rec, transforms_ok=False))
    out = dispatch["clean_and_dedupe"]("clean_and_dedupe", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload", "clean"]
    # deduplicate never ran
    assert not any(k == "dedup" for k, _ in rec)


def test_all_four_composites_exist_no_dangling_curated():
    from goldensuite_mcp.server import _aggregate, CURATED_TOOLS
    import os
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    tools, _ = _aggregate()
    names = {t.name for t in tools}
    for c in ("dedupe_file", "assess_file", "match_sources", "clean_and_dedupe"):
        assert c in names, f"{c} not registered"
    # every composite curated name now resolves to a real tool
    assert {"dedupe_file", "assess_file", "match_sources", "clean_and_dedupe"} <= (CURATED_TOOLS & names)
