"""Unit tests for the workflow-YAML gate.

The gate's value is entirely in the two things it must not do: miss a duplicate
key (which parses clean and wins last), and pass while scanning nothing.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_workflow_yaml as mod  # noqa: E402


def _load_ci_workflow() -> dict:
    """Parse the real `.github/workflows/ci.yml`, not a synthetic fixture.

    The wiring-reachability test below asserts something about the actual
    file this repo ships, not about a scratch workflow built by the `wf`
    fixture -- so it needs its own loader rather than that fixture.
    """
    ci_path = mod.WORKFLOW_DIR / "ci.yml"
    return yaml.safe_load(ci_path.read_text(encoding="utf-8"))


@pytest.fixture
def wf(tmp_path):
    """Write workflow files into a scratch workflow dir."""
    d = tmp_path / "workflows"
    d.mkdir()

    def write(name: str, src: str) -> Path:
        p = d / name
        p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
        return p

    write.dir = d  # type: ignore[attr-defined]
    return write


def test_duplicate_key_is_caught(wf):
    """The bench-er-kg shape: a second `if:` on a step that already had one."""
    wf(
        "dup.yml",
        """
        name: x
        jobs:
          build:
            steps:
              - name: upload
                if: always()
                if: github.event_name != 'schedule'
                run: echo hi
        """,
    )
    problems, scanned = mod.check(wf.dir)
    assert scanned == 1
    assert len(problems) == 1
    assert "duplicate key `if`" in problems[0][1]


def test_safe_load_would_have_missed_it(wf):
    """Pins WHY this gate exists: the stock loader accepts the same document."""
    import yaml

    p = wf(
        "dup.yml",
        """
        steps:
          - if: always()
            if: never()
        """,
    )
    # No exception, and the first value is gone.
    loaded = yaml.safe_load(p.read_text())
    assert loaded["steps"][0]["if"] == "never()"
    assert mod.check(wf.dir)[0], "the strict loader must reject what safe_load accepts"


def test_clean_workflows_pass(wf):
    wf(
        "ok.yml",
        """
        name: fine
        on:
          push:
            branches: [main]
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: echo ok
        """,
    )
    assert mod.check(wf.dir)[0] == []


def test_duplicate_in_a_nested_mapping_is_caught(wf):
    wf(
        "nested.yml",
        """
        jobs:
          a:
            env:
              FOO: 1
              FOO: 2
        """,
    )
    problems, _ = mod.check(wf.dir)
    assert len(problems) == 1
    assert "FOO" in problems[0][1]


def test_repeated_key_in_different_mappings_is_fine(wf):
    """Two jobs may each have `runs-on` -- only same-mapping repeats are errors."""
    wf(
        "two-jobs.yml",
        """
        jobs:
          a:
            runs-on: ubuntu-latest
          b:
            runs-on: ubuntu-latest
        """,
    )
    assert mod.check(wf.dir)[0] == []


def test_unparseable_file_is_reported(wf):
    wf("broken.yml", "jobs:\n  - [unclosed\n")
    problems, _ = mod.check(wf.dir)
    assert len(problems) == 1
    assert "does not parse" in problems[0][1]


def test_non_yaml_files_are_ignored(wf):
    wf("notes.md", "# not a workflow\n")
    wf("ok.yml", "name: fine\n")
    _, scanned = mod.check(wf.dir)
    assert scanned == 1


def test_empty_scan_is_reported_as_broken(wf, capsys):
    rc = mod.main(["--dir", str(wf.dir)])
    assert rc == 2
    assert "the scan is broken" in capsys.readouterr().err


def test_missing_directory_is_reported_as_broken(tmp_path, capsys):
    rc = mod.main(["--dir", str(tmp_path / "nope")])
    assert rc == 2
    assert "the scan is broken" in capsys.readouterr().err


def test_the_repo_as_it_stands_passes():
    """End-to-end on the real workflow dir -- the state the gate must hold."""
    problems, scanned = mod.check(mod.WORKFLOW_DIR)
    assert scanned >= mod.MIN_EXPECTED_WORKFLOWS, scanned
    assert problems == [], problems


def test_sync_claims_job_is_reachable():
    """A job whose gating output is never emitted is skipped on every run.

    The `changes` job needs an explicit `outputs:` line per filter. Without it
    `needs.changes.outputs.sync_claims` is empty, the `if:` is false, and the
    lane reports green having measured nothing -- PR #2839's defect.
    """
    spec = _load_ci_workflow()
    outputs = spec["jobs"]["changes"]["outputs"]
    assert "sync_claims" in outputs, (
        "the changes job emits no sync_claims output, so the job can never run"
    )
    assert "sync_claims" in spec["jobs"]["sync_claims"]["if"]


def test_shard_jobs_carry_cov_context():
    """Without --cov-context=test on BOTH shard-producing jobs, the .coverage
    file sync_claims combines has no per-test data at all, and coverage-based
    enforcement silently finds nothing -- the exact failure mode this whole
    mechanism exists to avoid, one level up the chain."""
    spec = _load_ci_workflow()
    for job_name in ("python_goldenmatch", "python_goldenmatch_heavy"):
        steps = spec["jobs"][job_name]["steps"]
        cov_steps = [s for s in steps if "--cov=goldenmatch" in (s.get("run") or "")]
        assert cov_steps, f"{job_name} has no --cov=goldenmatch step to check"
        assert any("--cov-context=test" in s["run"] for s in cov_steps), (
            f"{job_name}'s coverage step is missing --cov-context=test"
        )


def test_sync_claims_depends_on_the_shard_jobs():
    spec = _load_ci_workflow()
    needs = spec["jobs"]["sync_claims"]["needs"]
    assert "python_goldenmatch" in needs
    assert "python_goldenmatch_heavy" in needs


def test_sync_claims_degrades_when_shard_jobs_are_skipped():
    """The `if:` must tolerate SKIPPED (not require success()) on both shard
    jobs, or sync_claims never runs at all on a PR that does not touch
    goldenmatch code -- exactly the scenario coverage-based enforcement must
    degrade through, not disappear under."""
    spec = _load_ci_workflow()
    job = spec["jobs"]["sync_claims"]
    condition = job["if"]
    assert "always()" in condition, (
        "the if: must start from always() or an implicit success() re-requires "
        "both shard jobs to have run, defeating graceful degradation"
    )
    for dep in ("python_goldenmatch", "python_goldenmatch_heavy"):
        assert f"needs.{dep}.result != 'failure'" in condition
        assert f"needs.{dep}.result != 'cancelled'" in condition


def test_sync_claims_downloads_coverage_shards_and_passes_the_flag():
    spec = _load_ci_workflow()
    steps = spec["jobs"]["sync_claims"]["steps"]
    download_steps = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact")]
    assert download_steps, "sync_claims has no download-artifact step"
    assert download_steps[0].get("with", {}).get("pattern") == "gm-cov-*"
    report_steps = [s for s in steps if "sync_claims.report" in (s.get("run") or "")]
    assert report_steps, "sync_claims has no report step"
    assert "--coverage-db" in report_steps[0]["run"], (
        "the report step never passes --coverage-db even conditionally"
    )
