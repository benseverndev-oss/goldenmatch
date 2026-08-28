"""Unit tests for the distributed test partition.

The partition's value is entirely in two things it must not do: let a gate file
leak into the broad job (which is how 106 of 181 tests became duplicates), and
report a clean partition while scanning an empty directory.

Spec: docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import distributed_test_files as mod  # noqa: E402


@pytest.fixture
def tests_dir(tmp_path):
    d = tmp_path / "tests"
    d.mkdir()
    for name in (
        "test_distributed_clustering.py",
        "test_distributed_randomized_contraction_wcc.py",
        "test_distributed_golden.py",
        "test_distributed_pipeline.py",
        "test_unrelated.py",
    ):
        (d / name).write_text("", encoding="utf-8")
    return d


def test_gate_files_go_to_their_own_jobs(tests_dir):
    part = mod.partition(tests_dir)
    assert [p.name for p in part["invariance"]] == ["test_distributed_clustering.py"]
    assert [p.name for p in part["wcc"]] == ["test_distributed_randomized_contraction_wcc.py"]


def test_gate_files_never_appear_in_broad(tests_dir):
    """The bug this module exists to prevent: --ignore is a no-op against
    explicitly-named paths, so the glob re-ran both blocking gates."""
    broad = {p.name for p in mod.partition(tests_dir)["broad"]}
    assert "test_distributed_clustering.py" not in broad
    assert "test_distributed_randomized_contraction_wcc.py" not in broad


def test_broad_takes_every_other_distributed_file(tests_dir):
    broad = {p.name for p in mod.partition(tests_dir)["broad"]}
    assert broad == {"test_distributed_golden.py", "test_distributed_pipeline.py"}


def test_non_distributed_files_are_not_claimed(tests_dir):
    everything = {p.name for files in mod.partition(tests_dir).values() for p in files}
    assert "test_unrelated.py" not in everything


def test_partition_is_disjoint(tests_dir):
    part = mod.partition(tests_dir)
    seen = [p for files in part.values() for p in files]
    assert len(seen) == len(set(seen))


def test_empty_directory_raises_rather_than_passing_clean(tmp_path):
    """A partition over nothing is the 'gate that scans nothing' failure."""
    empty = tmp_path / "tests"
    empty.mkdir()
    with pytest.raises(SystemExit):
        mod.partition(empty)


def test_missing_gate_file_raises(tmp_path):
    """If a gate file is renamed, fail loudly instead of silently gating nothing."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_distributed_golden.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.partition(d)


def test_main_prints_one_path_per_line(tests_dir, capsys):
    rc = mod.main(["--job", "broad", "--tests-dir", str(tests_dir)])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert all(line.endswith(".py") for line in lines)


def test_main_rejects_an_unknown_job(tests_dir):
    with pytest.raises(SystemExit):
        mod.main(["--job", "nope", "--tests-dir", str(tests_dir)])
