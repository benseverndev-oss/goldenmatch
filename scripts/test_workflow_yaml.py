"""Unit tests for the workflow-YAML gate.

The gate's value is entirely in the two things it must not do: miss a duplicate
key (which parses clean and wins last), and pass while scanning nothing.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_workflow_yaml as mod  # noqa: E402


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
