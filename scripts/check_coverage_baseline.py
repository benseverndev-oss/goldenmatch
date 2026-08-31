"""Universal per-module coverage anti-regression gate.

The curated floors in `check_coverage_floors.py` cover the modules someone wrote
an opinion about -- 38 of 436. This covers the rest: every measured module has a
recorded rate in `coverage_baseline.json`, and dropping below it fails.

Three ways to fail, all of them actionable:

1. REGRESSION  -- a module fell more than its tolerance below baseline.
2. NEW MODULE  -- measured but not in the baseline. Fails rather than being
   accepted silently, because "untested code lands unnoticed" is the exact hole
   this gate exists to close. One regen command records it.
3. STALE ENTRY -- in the baseline but no longer measured. Fails for the same
   reason a floor on a deleted module fails: an entry that cannot be evaluated
   is not protection, and leaving it there hides that the gate shrank.

Usage:
    python scripts/check_coverage_baseline.py packages/python/goldenmatch/coverage.xml
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coverage_baseline import (  # noqa: E402
    load_baseline,
    parse_report,
    tolerance_for,
)

REGEN = "python scripts/regen_coverage_baseline.py <coverage.xml>"


def compare(measured: dict[str, dict], baseline: dict[str, dict]) -> dict[str, list[str]]:
    """Return {category: [messages]} -- empty dict means the gate passes."""
    problems: dict[str, list[str]] = {"regressed": [], "new": [], "stale": []}

    for name, info in sorted(measured.items()):
        prev = baseline.get(name)
        if prev is None:
            problems["new"].append(
                f"{name}: {info['rate']:.1%} over {info['statements']} statements"
            )
            continue
        tol = tolerance_for(prev.get("statements") or info["statements"])
        if info["rate"] < prev["rate"] - tol:
            problems["regressed"].append(
                f"{name}: {info['rate']:.1%} < baseline {prev['rate']:.1%} "
                f"(tolerance {tol:.1%})"
            )

    for name in sorted(set(baseline) - set(measured)):
        problems["stale"].append(f"{name}: in the baseline but not measured")

    return {k: v for k, v in problems.items() if v}


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {Path(sys.argv[0]).name} <coverage.xml>", file=sys.stderr)
        return 2
    xml_path = Path(sys.argv[1])
    if not xml_path.exists():
        print(f"coverage.xml not found: {xml_path}", file=sys.stderr)
        return 2

    measured = parse_report(xml_path)
    baseline = load_baseline().get("modules", {})
    if not baseline:
        print("coverage_baseline.json has no modules -- the gate would pass "
              "vacuously. Regenerate it.", file=sys.stderr)
        return 2

    problems = compare(measured, baseline)
    if not problems:
        print(f"Coverage baseline OK: {len(measured)} modules, none regressed.")
        return 0

    if problems.get("regressed"):
        print("Coverage REGRESSED below baseline:")
        for m in problems["regressed"]:
            print(f"  - {m}")
        print("  Add tests. Only lower a baseline deliberately, with a reason:")
        print(f"    {REGEN} --allow-lower")
        print()
    if problems.get("new"):
        print("Modules measured but NOT in the baseline (new or renamed):")
        for m in problems["new"]:
            print(f"  - {m}")
        print(f"  Record them: {REGEN}")
        print()
    if problems.get("stale"):
        print("Baseline entries no longer measured (deleted, renamed, or newly omitted):")
        for m in problems["stale"]:
            print(f"  - {m}")
        print(f"  Prune them: {REGEN}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
