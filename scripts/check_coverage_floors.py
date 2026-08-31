"""Per-module coverage floor enforcement.

Reads `coverage.xml` (produced by `pytest --cov --cov-report=xml`) and asserts
each module group meets its declared floor. This guards against per-module
regressions hiding inside the global average — e.g. a 50%-coverage package
masquerading inside a 72% global.

Floors are intentionally conservative; ratchet upward over time.

Usage:
    pytest --cov=goldenmatch --cov-report=xml --cov-report=term-missing
    python scripts/check_coverage_floors.py packages/python/goldenmatch/coverage.xml
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coverage_paths import normalize  # noqa: E402

# Module-prefix → minimum line-rate (0.0–1.0). Prefixes match `<class
# filename="goldenmatch/...">` in coverage.xml.
#
# Conservative starting floors — set ~5pp below today's measured value so a
# real regression trips but a 1-2pp wobble doesn't. Ratchet upward as packages
# improve.
FLOORS: dict[str, float] = {
    # ---------------------------------------------------------------- core
    # Floors sit ~3pp under the measured value: enough to absorb shard-to-shard
    # wobble, tight enough that losing a test file trips something. Measured
    # 2026-08-31 from the combined CI shards of run 33435122397.
    "goldenmatch/core/scorer.py": 0.85,                    # 88.1%
    "goldenmatch/core/pipeline.py": 0.81,                  # 84.2%
    "goldenmatch/core/probabilistic.py": 0.90,             # 93.0%
    "goldenmatch/core/cluster.py": 0.82,                   # 85.0%
    "goldenmatch/core/frame.py": 0.93,                     # 96.8%  (1148 stmts)
    "goldenmatch/core/golden.py": 0.89,                    # 92.5%
    "goldenmatch/core/golden_fused.py": 0.89,              # 92.0%
    "goldenmatch/core/blocker.py": 0.80,                   # 83.2%
    "goldenmatch/core/smart_ingest.py": 0.80,              # 83.6%
    "goldenmatch/core/config_critique.py": 0.77,           # 80.3%
    "goldenmatch/core/llm_scorer.py": 0.83,                # 86.1%
    # ---------------------------------------------------------- auto-config
    "goldenmatch/core/autoconfig.py": 0.88,                # 91.4%
    "goldenmatch/core/autoconfig_controller.py": 0.85,     # 88.2%
    "goldenmatch/core/autoconfig_rules.py": 0.93,          # 96.5%
    "goldenmatch/core/autoconfig_negative_evidence.py": 0.92,  # 95.7%
    "goldenmatch/core/autoconfig_verify.py": 0.91,         # 94.7%
    "goldenmatch/core/indicators.py": 0.84,                # 86.7%
    "goldenmatch/core/complexity_profile.py": 0.94,        # 97.1%
    # -------------------------------------------------------------- backends
    "goldenmatch/backends/score_buckets.py": 0.81,         # 84.7%  (1025 stmts)
    "goldenmatch/backends/fs_out_of_core.py": 0.88,        # 91.7%
    # -------------------------------------------------------------- identity
    "goldenmatch/identity/store.py": 0.75,                 # 78.6%  (1022 stmts)
    "goldenmatch/identity/resolve.py": 0.89,               # 92.9%
    "goldenmatch/identity/snowflake_backend.py": 0.86,     # 89.3%
    # ---------------------------------------------------------------- other
    "goldenmatch/semantic/ontology.py": 0.92,              # 95.9%
    "goldenmatch/config/": 0.84,                           # 87.4% (n=12)
    "goldenmatch/core/memory/": 0.92,                      # 95.0% (n=4)
    "goldenmatch/pprl/": 0.93,                             # 96.4% (n=3)
    "goldenmatch/_api.py": 0.85,                           # 88.4%

    # ---------------------------------------------------------------- CLI
    # What users actually type. cli/main.py wires every command and defines
    # several inline, so a drop here means a verb stopped working, not a metric
    # moved. 52.9% -> 64% (tests/test_cli_surface.py); the remainder is the
    # `profile` and `analyze-blocking` bodies, both polars-bound (see the strict
    # xfails in that file) and the TUI launchers.
    "goldenmatch/cli/main.py": 0.61,                       # 64.0%  (272 stmts)
    "goldenmatch/prefs/store.py": 0.82,                    # 85.0%

    # ------------------------------------------------------- DECAY GUARDS --
    # These are NOT targets. They are large, poorly-covered modules pinned ~3pp
    # under today's value so the debt cannot quietly deepen. Raising them is
    # real work; the point of listing them is that the number is visible and
    # one-way. Do not "fix" a failure here by lowering the floor.
    "goldenmatch/distributed/clustering.py": 0.15,         # 18.8%  (576 stmts)
    "goldenmatch/spark/config_pipeline.py": 0.23,          # 26.2%  (485 stmts)
    "goldenmatch/core/boost.py": 0.03,                     #  5.8%  (397 stmts)
    "goldenmatch/tui/app.py": 0.44,                        # 47.6%
    "goldenmatch/a2a/skills.py": 0.14,                     # 17.4%
    "goldenmatch/tui/tabs/boost_tab.py": 0.27,             # 30.5%
    "goldenmatch/tui/tabs/config_tab.py": 0.32,            # 35.5%
    "goldenmatch/cli/evaluate.py": 0.36,                   # 39.9%
    "goldenmatch/spark/identity.py": 0.15,                 # 18.4%
    "goldenmatch/cli/identity.py": 0.42,                   # 45.1%
}


def parse_coverage(xml_path: Path) -> dict[str, float]:
    """Returns module-filename → line-rate."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rates: dict[str, float] = {}
    for cls in root.iter("class"):
        # Normalize: CI's report shape and this table's keys were written
        # independently and did not match, so every floor missed. See
        # scripts/coverage_paths.py.
        filename = normalize(cls.get("filename") or "")
        line_rate = float(cls.get("line-rate") or "0")
        rates[filename] = line_rate
    return rates


def check(rates: dict[str, float], floors: dict[str, float]) -> tuple[list[str], int]:
    """Return (failures, n_checked).

    A floor naming something absent from the coverage report is a FAILURE, not a
    warning. It used to `continue`, and `main` then printed "All N module floors
    met" using ``len(FLOORS)`` — so a floor pointing at a deleted module reported
    success for a check that never ran. ``goldenmatch/core/engine.py`` sat in this
    list after the module was gone, and CI reported 15 floors met while
    evaluating 14. A gate that cannot fail is worse than no gate, because it
    occupies the space where a real one would go.

    ``n_checked`` is returned so the caller reports the number actually
    evaluated rather than the size of the dict.
    """
    failures: list[str] = []
    n_checked = 0
    for prefix, floor in floors.items():
        # If prefix ends with /, treat as directory — aggregate all matching modules
        if prefix.endswith("/"):
            matching = {f: r for f, r in rates.items() if f.startswith(prefix)}
            if not matching:
                failures.append(
                    f"{prefix}: no modules under this prefix in the coverage "
                    f"report -- the floor matches nothing. Fix the prefix or "
                    f"drop the entry."
                )
                continue
            n_checked += 1
            # Weighted average by line count would be more accurate; for v1
            # the simple mean is good enough as a regression tripwire.
            avg = sum(matching.values()) / len(matching)
            if avg < floor:
                failures.append(
                    f"{prefix} (n={len(matching)}): "
                    f"actual={avg:.1%} < floor={floor:.1%}"
                )
        else:
            actual = rates.get(prefix)
            if actual is None:
                failures.append(
                    f"{prefix}: not in the coverage report -- the module was "
                    f"renamed, deleted, or is excluded by `omit`. This floor is "
                    f"checking nothing. Fix the path or drop the entry."
                )
                continue
            n_checked += 1
            if actual < floor:
                failures.append(
                    f"{prefix}: actual={actual:.1%} < floor={floor:.1%}"
                )
    return failures, n_checked


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_coverage_floors.py <coverage.xml>", file=sys.stderr)
        return 2
    xml_path = Path(sys.argv[1])
    if not xml_path.exists():
        print(f"coverage.xml not found: {xml_path}", file=sys.stderr)
        return 2
    rates = parse_coverage(xml_path)
    failures, n_checked = check(rates, FLOORS)

    if rates and n_checked == 0:
        # Every floor missed. That is a NAMING mismatch, not 38 deleted modules,
        # and it is the failure the old gate hid behind warnings. Show what the
        # report actually holds so one CI run is enough to diagnose it.
        print("NO floor matched any module -- this is a path-shape mismatch, "
              "not a coverage problem.")
        print(f"The report contains {len(rates)} modules, e.g.:")
        for name in sorted(rates)[:5]:
            print(f"    {name}")
        print()
    if failures:
        print("Per-module coverage floors NOT met:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("A 'not in the coverage report' entry means the floor is dead --")
        print("fix the path or delete it; do NOT leave it in place.")
        print("For a genuine drop, add tests for the regressed module. Lower a")
        print("floor only with a stated reason -- the DECAY GUARDS are one-way.")
        return 1
    # Report what was actually evaluated, not len(FLOORS): they differ the
    # moment an entry stops matching, which is exactly when you need to know.
    print(f"All {n_checked} module floors met (of {len(FLOORS)} declared).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
