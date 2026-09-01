"""Pins the measured-vs-executed distinction assert_sweep_coverage.py exists to draw.

`source = goldenmatch` in `.coveragerc-sweep` makes coverage.py enumerate every
file under the package and record it as MEASURED whether or not it ever ran.
A file with an empty ``lines()`` result was measured but never EXECUTED. The
old check (`len(measured_files()) >= 50`) could not tell these apart, so it
passed at 487 measured files even when the subprocess coverage hook captured
nothing at all -- PR #2836's CI log, exactly reproduced below as a fixture.

Uses `coverage.CoverageData` directly (`add_lines`, `write`) so these tests
build their own tiny fixture files rather than depending on a real sweep run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import coverage

sys.path.insert(0, str(Path(__file__).parent))

import assert_sweep_coverage  # noqa: E402

DAT_NAMES = ("coverage_sweep_cli.dat", "coverage_sweep_mcp.dat")


def _write_dat(basename: str, *, measured_only: int, executed: int) -> None:
    """Write a .dat file with `measured_only` never-ran files plus `executed`
    files that each have one real executed line -- mirroring exactly what
    `source = goldenmatch` produces: every package file gets an entry, some
    with hits and most (in the broken-hook case) without.
    """
    data = coverage.CoverageData(basename=basename)
    lines = {f"goldenmatch/never_ran_{i}.py": [] for i in range(measured_only)}
    lines.update({f"goldenmatch/ran_{i}.py": [1] for i in range(executed)})
    data.add_lines(lines)
    data.write()


def test_measured_but_nothing_executed_fails(tmp_path, monkeypatch, capsys):
    """Reproduces PR #2836 exactly: 487 measured files, zero of them ever ran
    -- the shape a completely broken subprocess coverage hook produces.
    """
    monkeypatch.chdir(tmp_path)
    for name in DAT_NAMES:
        _write_dat(name, measured_only=487, executed=0)

    rc = assert_sweep_coverage.main()

    assert rc == 1, "487 measured / 0 executed must fail, not pass at 487"
    err = capsys.readouterr().err
    assert "0 files with executed lines" in err
    assert "not working" in err


def test_enough_executed_files_passes(tmp_path, monkeypatch, capsys):
    """A healthy sweep: most of the package is merely measured (source =
    goldenmatch enumerates it), but comfortably more than the floor actually
    ran.
    """
    monkeypatch.chdir(tmp_path)
    for name in DAT_NAMES:
        _write_dat(name, measured_only=337, executed=150)

    rc = assert_sweep_coverage.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "487 measured, 150 with executed lines" in out


def test_failure_message_distinguishes_measured_from_executed(tmp_path, monkeypatch, capsys):
    """The failure output must name BOTH numbers and say why measured-but-
    unexecuted is the trap, not just report a bare count below a floor.
    """
    monkeypatch.chdir(tmp_path)
    _write_dat("coverage_sweep_cli.dat", measured_only=487, executed=0)
    _write_dat("coverage_sweep_mcp.dat", measured_only=487, executed=0)

    rc = assert_sweep_coverage.main()

    assert rc == 1
    captured = capsys.readouterr()
    # Both counts appear together, per .dat file, in the informational line --
    # a reader must be able to see 487 measured vs 0 executed side by side.
    assert "coverage_sweep_cli.dat: 487 measured, 0 with executed lines" in captured.out
    assert "coverage_sweep_mcp.dat: 487 measured, 0 with executed lines" in captured.out
    # The failure explanation on stderr must name the trap by name: a high
    # measured count is not evidence anything ran.
    assert "487 files were" in captured.err
    assert "measured-but-never-executed" in captured.err
