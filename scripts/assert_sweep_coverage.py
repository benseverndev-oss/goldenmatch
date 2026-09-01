"""Fail if a sweep coverage file exists but measured nothing.

A present-but-empty .dat combines cleanly and reports success, which is exactly
how a coverage union comes to measure nil while every check stays green.
"""

from __future__ import annotations

import sys

import coverage

MIN_FILES = 50


def main() -> int:
    for name in ("coverage_sweep_cli.dat", "coverage_sweep_mcp.dat"):
        data = coverage.CoverageData(basename=name)
        data.read()
        measured = list(data.measured_files())
        print(f"{name}: {len(measured)} measured files")
        if len(measured) < MIN_FILES:
            print(
                f"FAIL {name} measured {len(measured)} files, expected >= {MIN_FILES}. "
                "The subprocess coverage hook is not working.",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
