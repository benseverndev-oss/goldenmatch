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

# Module-prefix → minimum line-rate (0.0–1.0). Prefixes match `<class
# filename="goldenmatch/...">` in coverage.xml.
#
# Conservative starting floors — set ~5pp below today's measured value so a
# real regression trips but a 1-2pp wobble doesn't. Ratchet upward as packages
# improve.
FLOORS: dict[str, float] = {
    # Core scoring / pipeline — most-touched code; high coverage required
    "goldenmatch/core/scorer.py": 0.82,
    "goldenmatch/core/pipeline.py": 0.77,
    "goldenmatch/core/probabilistic.py": 0.91,
    # Auto-config — heavily tested via v1.8-v1.12 work
    "goldenmatch/core/autoconfig.py": 0.80,
    "goldenmatch/core/autoconfig_controller.py": 0.85,
    "goldenmatch/core/autoconfig_rules.py": 0.85,
    "goldenmatch/core/autoconfig_negative_evidence.py": 0.90,
    "goldenmatch/core/indicators.py": 0.85,
    "goldenmatch/core/complexity_profile.py": 0.85,
    # Config schemas — Pydantic models; should be near-100%
    "goldenmatch/config/": 0.85,
    # Memory + corrections (v1.6 Learning Memory)
    "goldenmatch/core/memory/": 0.80,
    # PPRL — Bloom filters + protocols
    "goldenmatch/pprl/": 0.85,
    # Public API surface
    "goldenmatch/_api.py": 0.80,
    # Engine / clustering
    "goldenmatch/core/cluster.py": 0.80,
    "goldenmatch/core/engine.py": 0.75,
}


def parse_coverage(xml_path: Path) -> dict[str, float]:
    """Returns module-filename → line-rate."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rates: dict[str, float] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        line_rate = float(cls.get("line-rate") or "0")
        rates[filename] = line_rate
    return rates


def check(rates: dict[str, float], floors: dict[str, float]) -> list[str]:
    """Return list of failures: 'module: actual=X% < floor=Y%'."""
    failures: list[str] = []
    for prefix, floor in floors.items():
        # If prefix ends with /, treat as directory — aggregate all matching modules
        if prefix.endswith("/"):
            matching = {f: r for f, r in rates.items() if f.startswith(prefix)}
            if not matching:
                continue  # no modules under this prefix; skip silently
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
                # Module missing from coverage report — could be excluded or
                # renamed. Don't fail loudly; surface as a warning.
                print(f"::warning::module not in coverage report: {prefix}")
                continue
            if actual < floor:
                failures.append(
                    f"{prefix}: actual={actual:.1%} < floor={floor:.1%}"
                )
    return failures


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_coverage_floors.py <coverage.xml>", file=sys.stderr)
        return 2
    xml_path = Path(sys.argv[1])
    if not xml_path.exists():
        print(f"coverage.xml not found: {xml_path}", file=sys.stderr)
        return 2
    rates = parse_coverage(xml_path)
    failures = check(rates, FLOORS)
    if failures:
        print("Per-module coverage floors NOT met:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("Ratchet floors in scripts/check_coverage_floors.py if the drop")
        print("is intentional; otherwise add tests for the regressed module.")
        return 1
    print(f"All {len(FLOORS)} module floors met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
