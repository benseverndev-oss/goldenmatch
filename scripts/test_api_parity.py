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
    m = {"grpc_methods": {"shared": [], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unknown_surface" for f in fails)


def test_a2a_skills_surface_partitions_and_absent_is_skipped():
    py = {"package": "gm", "mcp_tools": ["a"], "cli_commands": ["c"], "a2a_skills": ["s1", "s2"]}
    ts = {"package": "gm", "mcp_tools": ["a"], "cli_commands": ["c"], "a2a_skills": ["s1"]}
    m = gate.init_manifest(py, ts)
    assert m["a2a_skills"] == {"shared": ["s1"], "python_only": ["s2"], "ts_only": []}
    assert gate.run_checks(m, py, ts) == []
    py2 = {"package": "im", "mcp_tools": ["a"], "cli_commands": ["c"]}
    ts2 = {"package": "im", "mcp_tools": ["a"], "cli_commands": ["c"]}
    m2 = gate.init_manifest(py2, ts2)
    assert "a2a_skills" not in m2
    assert gate.run_checks(m2, py2, ts2) == []


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


def _cov_manifest(kernel_backed, all_scorers, deferred):
    """Minimal manifest for the scorer-coverage gate."""
    return {
        "package": "gm",
        "scorers": {"shared": sorted(all_scorers), "python_only": [], "ts_only": []},
        "scorer_kernels": {"shared": sorted(kernel_backed), "python_only": [], "ts_only": []},
        "scorer_kernels_deferred": deferred,
    }


def test_scorer_coverage_all_covered_passes():
    m = _cov_manifest(
        kernel_backed={"exact", "jaro_winkler"},
        all_scorers={"exact", "jaro_winkler", "ensemble", "phash"},
        deferred={"ensemble": "declined -- reason", "phash": "deferred -- reason"},
    )
    assert gate.check_scorer_coverage(m) == []


def test_scorer_coverage_uncovered_scorer_fails():
    # `phash` has no kernel and no deferral -> must be classified.
    m = _cov_manifest(
        kernel_backed={"exact"},
        all_scorers={"exact", "ensemble", "phash"},
        deferred={"ensemble": "declined -- reason"},
    )
    fails = gate.check_scorer_coverage(m)
    assert kinds(fails) == ["uncovered_scorer"]
    assert fails[0].name == "phash"


def test_scorer_coverage_regression_kernel_to_fallback_fails():
    # A scorer that WAS kernel-backed is dropped from scorer_kernels without a
    # deferral -> lands as uncovered. This is the coverage floor: coverage can't
    # silently regress.
    m = _cov_manifest(
        kernel_backed={"exact"},                    # qgram removed from kernels
        all_scorers={"exact", "qgram"},
        deferred={},
    )
    fails = gate.check_scorer_coverage(m)
    assert kinds(fails) == ["uncovered_scorer"]
    assert fails[0].name == "qgram"


def test_scorer_coverage_stale_deferral_fails():
    # `qgram` is kernel-backed but still listed as deferred -> remove the annotation.
    m = _cov_manifest(
        kernel_backed={"exact", "qgram"},
        all_scorers={"exact", "qgram"},
        deferred={"qgram": "deferred -- stale"},
    )
    fails = gate.check_scorer_coverage(m)
    assert kinds(fails) == ["stale_deferral"]


def test_scorer_coverage_unknown_deferral_fails():
    # `bogus` is deferred but not a real scorer.
    m = _cov_manifest(
        kernel_backed={"exact"},
        all_scorers={"exact"},
        deferred={"bogus": "deferred -- typo"},
    )
    fails = gate.check_scorer_coverage(m)
    assert kinds(fails) == ["unknown_deferral"]


def test_scorer_coverage_missing_reason_fails():
    for empty in ("", "   ", None):
        m = _cov_manifest(
            kernel_backed={"exact"},
            all_scorers={"exact", "phash"},
            deferred={"phash": empty},
        )
        fails = gate.check_scorer_coverage(m)
        assert kinds(fails) == ["missing_reason"], empty


def test_scorer_coverage_absent_surfaces_skipped():
    # Packages without a scorer surface (goldencheck, ...) are unaffected.
    assert gate.check_scorer_coverage({"package": "goldencheck"}) == []


def test_scorer_coverage_malformed_deferred_fails():
    m = _cov_manifest(kernel_backed=set(), all_scorers={"exact"}, deferred=["not", "a", "map"])
    fails = gate.check_scorer_coverage(m)
    assert kinds(fails) == ["malformed_deferred"]


def test_structure_allows_deferred_map():
    # scorer_kernels_deferred is a classification map, not a partition surface.
    m = {"scorer_kernels_deferred": {"phash": "deferred -- reason"}}
    assert not any(f.kind == "unknown_surface" for f in gate.check_structure(m))


import os, subprocess, sys, json, pathlib

def test_python_emitter_goldenmatch_smoke():
    """Runs the real emitter against goldenmatch. Box-safe (needs goldenmatch[mcp] in the venv)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    env = {**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "GOLDENMATCH_NATIVE": "0",
           "PYTHONPATH": str(root / "packages" / "python" / "goldenmatch")}
    proc = subprocess.run([sys.executable, str(root / "scripts" / "emit_python_surface.py"), "goldenmatch"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    desc = json.loads(proc.stdout)
    assert desc["package"] == "goldenmatch"
    assert desc["mcp_tools"] == sorted(desc["mcp_tools"]) and desc["mcp_tools"]
    assert desc["cli_commands"] == sorted(desc["cli_commands"]) and desc["cli_commands"]
    # MCP count equals the MEASURED len(TOOLS) — never a hardcoded number
    from goldenmatch.mcp.server import TOOLS
    assert len(desc["mcp_tools"]) == len(TOOLS)
    # known real names present
    assert "find_duplicates" in desc["mcp_tools"]
    assert "mcp-serve" in desc["cli_commands"] and "identity" in desc["cli_commands"]
    # a2a_skills present + sorted; count/content MEASURED off the real _SKILLS, never hardcoded
    from goldenmatch.a2a.server import _SKILLS
    assert desc["a2a_skills"] == sorted(s["id"] for s in _SKILLS)


import pytest


@pytest.mark.parametrize(
    "pkg", ["goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "goldenanalysis", "infermap"]
)
def test_python_emitter_all_packages_smoke(pkg):
    """Every package's Python emitter produces a non-empty, sorted mcp+cli surface. Box-safe
    (needs each <pkg>[mcp] in the venv)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    env = {**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "GOLDENMATCH_NATIVE": "0",
           "GOLDENFLOW_NATIVE": "0", "GOLDENCHECK_NATIVE": "0",
           "PYTHONPATH": str(root / "packages" / "python" / pkg)}
    proc = subprocess.run([sys.executable, str(root / "scripts" / "emit_python_surface.py"), pkg],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    desc = json.loads(proc.stdout)
    assert desc["package"] == pkg
    for surface in ("mcp_tools", "cli_commands"):
        assert desc[surface] == sorted(desc[surface]) and desc[surface], f"{pkg}.{surface} empty/unsorted"
    # a2a_skills: present + non-empty + sorted for the 4 A2A packages, absent for the rest
    if pkg in ("goldenmatch", "goldencheck", "goldenflow", "goldenpipe"):
        assert desc["a2a_skills"] == sorted(desc["a2a_skills"]) and desc["a2a_skills"], \
            f"{pkg}.a2a_skills empty/unsorted"
    else:
        assert "a2a_skills" not in desc, f"{pkg} should not emit a2a_skills"
