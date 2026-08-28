"""Owns the partition of tests/test_distributed_*.py across the three
distributed CI jobs.

`.github/workflows/ci.yml` used to express the split as a shell glob plus two
`--ignore` flags. The glob expands to explicit paths and `--ignore` only filters
directory traversal, so both flags were no-ops: on run 33176961803 the
`distributed_broad` job collected 181 tests, of which 86 came from
test_distributed_clustering.py and test_distributed_randomized_contraction_wcc.py
-- a second run of the two jobs that already block the merge queue.

Confirmed by the fix: the same job on run 33197155069 collected 95 (181 - 86) and
went from 669s to 69.5s wall.

Each job now asks this module for its file list, so the partition is executed
rather than asserted.

Usage::

    python3 scripts/distributed_test_files.py --job broad

Spec: docs/superpowers/specs/2026-08-28-ray-production-readiness-design.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEFAULT_TESTS_DIR = Path("packages/python/goldenmatch/tests")

# Files with their own blocking job. Everything else matching the glob is broad.
GATED: dict[str, str] = {
    "invariance": "test_distributed_clustering.py",
    "wcc": "test_distributed_randomized_contraction_wcc.py",
}

_GLOB = "test_distributed_*.py"


def partition(tests_dir: Path) -> dict[str, list[Path]]:
    """Split the distributed test files across the three jobs.

    Exits non-zero rather than returning an empty or incomplete partition: a
    gate that reports clean while scanning nothing is the failure mode this
    module was written to remove.
    """
    found = sorted(tests_dir.glob(_GLOB))
    if not found:
        sys.exit(f"error: no {_GLOB} files under {tests_dir} -- the partition would gate nothing")

    by_name = {p.name: p for p in found}
    missing = [name for name in GATED.values() if name not in by_name]
    if missing:
        sys.exit(
            "error: gated file(s) not found: "
            + ", ".join(missing)
            + " -- if a gate file was renamed, update GATED in this module"
        )

    result: dict[str, list[Path]] = {job: [by_name[name]] for job, name in GATED.items()}
    result["broad"] = [p for p in found if p.name not in set(GATED.values())]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", required=True, choices=["invariance", "wcc", "broad"])
    ap.add_argument("--tests-dir", default=str(_DEFAULT_TESTS_DIR))
    args = ap.parse_args(argv)

    for path in partition(Path(args.tests_dir))[args.job]:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
