"""Coverage must cross the sweep's subprocess boundary.

The sweeps run every command in a child process. Without an explicit
process-startup hook the parent's coverage records nothing, and the resulting
.dat file is a green artifact measuring nil.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).parent
REPO = SCRIPTS.parent
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def test_probe_source_starts_coverage_in_the_child():
    """Both sweeps must inject the startup hook into their generated probe."""
    sys.path.insert(0, str(SCRIPTS))
    from sweep_cli_polars_free import _probe_source as cli_probe
    from sweep_mcp_polars_free import _probe_source as mcp_probe

    for name, src in (("cli", cli_probe()), ("mcp", mcp_probe())):
        assert "COVERAGE_PROCESS_START" in src, f"{name} probe has no coverage hook"
        assert "coverage.process_startup()" in src, f"{name} probe never starts coverage"


def test_a_child_process_actually_records_coverage(tmp_path):
    """End-to-end: a child importing goldenmatch must produce its own data file."""
    rc = tmp_path / "rc"
    rc.write_text("[run]\nsource = goldenmatch\nparallel = True\n", encoding="utf-8")
    child = tmp_path / "child.py"
    child.write_text(
        textwrap.dedent(
            """
            import os
            if os.environ.get("COVERAGE_PROCESS_START"):
                import coverage
                coverage.process_startup()
            import goldenmatch.core.frame  # noqa: F401
            """
        ),
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys\nsubprocess.run([sys.executable, sys.argv[1]], check=True)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(tmp_path / "c.dat")
    env["COVERAGE_PROCESS_START"] = str(rc)
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--rcfile", str(rc), str(parent), str(child)],
        check=True,
        env=env,
        cwd=str(tmp_path),
    )
    written = sorted(tmp_path.glob("c.dat*"))
    assert len(written) >= 2, f"expected a parent and a child data file, got {written}"


def test_relative_process_start_fails_from_a_different_cwd(tmp_path):
    """Reproduces the bug the workflow shipped with.

    run_sweep() launches the probe with cwd set to a scratch TemporaryDirectory,
    NOT the workspace the rcfile lives in. A COVERAGE_PROCESS_START relative to
    the workspace cannot be found from that scratch cwd, and coverage.py raises
    loudly (by design -- no try/except around the hook) instead of silently
    doing nothing. This pins that failure so nobody reintroduces a relative
    path in the workflow without a test noticing.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "rc.ini").write_text(
        "[run]\nsource = goldenmatch\nparallel = True\n", encoding="utf-8"
    )
    scratch_cwd = tmp_path / "scratch_cwd"
    scratch_cwd.mkdir()
    child = tmp_path / "child.py"
    child.write_text(
        textwrap.dedent(
            """
            import os
            if os.environ.get("COVERAGE_PROCESS_START"):
                import coverage
                coverage.process_startup()
            print("child ran")
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(tmp_path / "c2.dat")
    # Relative -- resolves against `workspace`, not `scratch_cwd`, exactly like
    # the original `COVERAGE_PROCESS_START: packages/python/.../.coveragerc-sweep`
    # did in the workflow before it was made absolute.
    env["COVERAGE_PROCESS_START"] = "rc.ini"
    result = subprocess.run(
        [sys.executable, str(child)],
        cwd=str(scratch_cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "expected a relative COVERAGE_PROCESS_START to fail to resolve from a "
        f"different cwd, but the child exited 0: stdout={result.stdout!r}"
    )
    assert "ConfigError" in result.stderr, result.stderr


def test_sweep_coverage_job_env_vars_are_absolute():
    """Wiring test: the workflow's COVERAGE_FILE and COVERAGE_PROCESS_START
    must be absolute (`${{ github.workspace }}/...`), or the sweep steps hit
    the exact failure pinned above the moment they run in CI.
    """
    with CI_WORKFLOW.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    job = doc["jobs"]["goldenmatch_sweep_coverage"]
    sweep_steps = [
        step
        for step in job["steps"]
        if isinstance(step.get("env"), dict)
        and ("COVERAGE_FILE" in step["env"] or "COVERAGE_PROCESS_START" in step["env"])
    ]
    assert len(sweep_steps) == 2, (
        f"expected 2 sweep steps with coverage env vars, got {sweep_steps}"
    )
    for step in sweep_steps:
        for key in ("COVERAGE_FILE", "COVERAGE_PROCESS_START"):
            value = step["env"][key]
            assert value.startswith("${{ github.workspace }}"), (
                f"{step.get('name', '?')}: {key} is not absolute: {value!r}"
            )
