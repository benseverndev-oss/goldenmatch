"""Fail if a sweep coverage file exists but measured nothing.

A present-but-empty .dat combines cleanly and reports success, which is exactly
how a coverage union comes to measure nil while every check stays green.

MEASURED IS NOT EXECUTED. Both sweeps run under ``.coveragerc-sweep``, which
sets ``source = goldenmatch``. That setting makes coverage.py enumerate every
file under the package and record a zero-line entry for each one, whether or
not it ever ran -- so ``CoverageData.measured_files()`` returns the full
487-file package count REGARDLESS of whether the subprocess coverage hook
worked at all. PR #2836's actual CI log printed
``coverage_sweep_cli.dat: 487 measured files`` and
``coverage_sweep_mcp.dat: 487 measured files`` -- identical counts from two
sweeps that exercise very different surfaces (66 CLI commands vs. 97 MCP
tools). That is the tell: a measured-files count of 487 is what you get
whether the hook captured real execution or nothing ran at all. It is exactly
the failure this script exists to catch, passing.

The fix: count files with EXECUTED lines (``CoverageData.lines(filename)``
non-empty), not merely measured ones. A measured-but-never-executed file
records no lines and this correctly does not count it.

Where MIN_EXECUTED_FILES comes from: running the real subprocess sweeps
locally is too slow/heavy for this check (that is the whole reason they run
in CI, not pre-commit). Instead, each entry point was imported once, in
process, under `coverage.py`, with polars and goldenmatch_native blocked at
the meta path the same way the sweep's own probe blocks polars -- i.e. no
subcommands or tools were invoked, only the module import graph reachable
from the entry point:

    from goldenmatch.cli.main import app        -> 178 files w/ executed lines
    from goldenmatch.mcp.server import TOOLS     -> 153 files w/ executed lines

(reproduced twice, with and without blocking polars/goldenmatch_native --
identical both times, so neither optional dependency is on the import-time
path). Those numbers are a HARD LOWER BOUND on what each real sweep executes:
the real sweeps additionally invoke 66 CLI commands and 97 MCP tools, which
can only run more code inside the modules already imported (and reach modules
that are imported lazily inside a command/tool body, never touched by a bare
import) -- never less. MIN_EXECUTED_FILES=100 sits comfortably below the
smaller of the two measured floors (153) to leave room for real CI/Linux vs.
local/Windows environment differences, while staying far above the 0 files
that measured_files() alone could never distinguish from a broken hook.
"""

from __future__ import annotations

import sys

import coverage

MIN_EXECUTED_FILES = 100


def main() -> int:
    failed = False
    for name in ("coverage_sweep_cli.dat", "coverage_sweep_mcp.dat"):
        data = coverage.CoverageData(basename=name)
        data.read()
        measured = list(data.measured_files())
        executed = [f for f in measured if data.lines(f)]
        print(f"{name}: {len(measured)} measured, {len(executed)} with executed lines")
        if len(executed) < MIN_EXECUTED_FILES:
            print(
                f"FAIL {name}: {len(executed)} files with executed lines, "
                f"expected >= {MIN_EXECUTED_FILES}. {len(measured)} files were "
                "MEASURED (coverage.py's `source = goldenmatch` records every "
                "package file whether or not it ran), but measured-but-never-"
                "executed is exactly the trap: it is what a broken subprocess "
                "coverage hook looks like too. The subprocess coverage hook is "
                "not working.",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
