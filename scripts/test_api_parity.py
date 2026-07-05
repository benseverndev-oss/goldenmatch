"""Unit tests for the API-parity gate. Pure data — no package imports, no YAML,
box-safe. Run: python -m pytest scripts/test_api_parity.py -q"""
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "check_api_parity", pathlib.Path(__file__).parent / "check_api_parity.py")
gate = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(gate)


def kinds(fails):
    return sorted(f.kind for f in fails)


def test_clean_partition_passes():
    m = {"shared": ["a", "b"], "python_only": ["p"], "ts_only": ["t"]}
    fails = gate.check_partition("mcp_tools", m, py={"a", "b", "p"}, ts={"a", "b", "t"})
    assert fails == []


def test_common_but_not_shared():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    assert kinds(fails) == ["unshared_common"]


def test_undeclared_python_only():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts=set())
    assert kinds(fails) == ["undeclared_py_only"]


def test_undeclared_ts_only():
    m = {"shared": [], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py=set(), ts={"x"})
    assert kinds(fails) == ["undeclared_ts_only"]


def test_shared_missing_from_one_language():
    m = {"shared": ["x"], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts=set())
    assert kinds(fails) == ["shared_missing_ts"]


def test_python_only_now_in_ts():
    m = {"shared": [], "python_only": ["x"], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    assert "py_only_in_ts" in kinds(fails)


def test_ts_only_now_in_python():
    m = {"shared": [], "python_only": [], "ts_only": ["x"]}
    fails = gate.check_partition("mcp_tools", m, py={"x"}, ts={"x"})
    assert "ts_only_in_py" in kinds(fails)


def test_phantom_manifest_entry():
    m = {"shared": ["ghost"], "python_only": [], "ts_only": []}
    fails = gate.check_partition("mcp_tools", m, py=set(), ts=set())
    assert kinds(fails) == ["phantom"]


def test_structure_flags_duplicate_across_partitions():
    m = {"mcp_tools": {"shared": ["x"], "python_only": ["x"], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "not_disjoint" for f in fails)


def test_structure_flags_unsorted():
    m = {"mcp_tools": {"shared": ["b", "a"], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unsorted" for f in fails)


def test_structure_flags_unknown_surface():
    m = {"a2a_skills": {"shared": [], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unknown_surface" for f in fails)


def test_structure_clean():
    m = {"mcp_tools": {"shared": ["a", "b"], "python_only": [], "ts_only": []}}
    assert gate.check_structure(m) == []


def test_init_manifest_partitions():
    py = {"package": "gm", "mcp_tools": ["a", "p"], "cli_commands": ["x"]}
    ts = {"package": "gm", "mcp_tools": ["a", "t"], "cli_commands": ["x"]}
    m = gate.init_manifest(py, ts)
    assert m["mcp_tools"] == {"shared": ["a"], "python_only": ["p"], "ts_only": ["t"]}
    assert m["cli_commands"] == {"shared": ["x"], "python_only": [], "ts_only": []}
    assert gate.run_checks(m, py, ts) == []


def test_run_checks_reports_across_surfaces():
    m = {"package": "gm",
         "mcp_tools": {"shared": [], "python_only": [], "ts_only": []},
         "cli_commands": {"shared": [], "python_only": [], "ts_only": []}}
    py = {"package": "gm", "mcp_tools": ["a"], "cli_commands": []}
    ts = {"package": "gm", "mcp_tools": [], "cli_commands": ["b"]}
    fails = gate.run_checks(m, py, ts)
    assert {f.kind for f in fails} == {"undeclared_py_only", "undeclared_ts_only"}
