#!/usr/bin/env python3
"""Emit the cross-language parity fixture for the resolution-tier undercount
confidence interval (`goldenmatch.semantic.key_integrity._wilson_interval` + the
`fragmented/resolved` point estimate).

The 95% Wilson score interval on the fragmentation rate is pure arithmetic
computed identically on Python and the TS port; this fixture (Python-generated)
locks them bit-for-bit. Read directly by both
`tests/test_undercount_ci.py` and
`tests/parity/undercount-ci.parity.test.ts` -- no copy.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.semantic.key_integrity import _wilson_interval

# (name, fragmented, resolved). Spans: no resolved entities (CI undefined),
# tiny-n wide interval, large-n tight interval, p at the 0 / 1 boundaries, and
# mid-range.
_CASES: list[tuple[str, int, int]] = [
    ("no_resolved", 0, 0),
    ("half_of_two", 1, 2),
    ("half_of_2000", 1, 2000),
    ("none_of_five", 0, 5),
    ("all_of_five", 5, 5),
    ("three_of_ten", 3, 10),
    ("half_of_hundred", 50, 100),
    ("none_of_one", 0, 1),
    ("all_of_one", 1, 1),
    ("seven_of_thirteen", 7, 13),
]

_OUT = (
    Path(__file__).resolve().parent.parent
    / ".."
    / ".."
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "key-integrity"
    / "undercount_ci_cases.json"
)


def _build() -> dict:
    cases = []
    for name, fragmented, resolved in _CASES:
        undercount = (fragmented / resolved) if resolved else 0.0
        ci = _wilson_interval(fragmented, resolved)
        cases.append(
            {
                "name": name,
                "fragmented": fragmented,
                "resolved": resolved,
                "expected": {
                    "undercount_estimate": undercount,
                    "ci_low": None if ci is None else ci[0],
                    "ci_high": None if ci is None else ci[1],
                },
            }
        )
    return {
        "_comment": (
            "Cross-language parity oracle for the resolution-tier undercount 95% "
            "Wilson score interval (bounds the SAMPLING uncertainty in "
            "fragmented/resolved). Generated from the Python reference "
            "goldenmatch.semantic.key_integrity._wilson_interval by "
            "scripts/emit_undercount_ci_fixture.py. Read DIRECTLY by both "
            "tests/test_undercount_ci.py and "
            "tests/parity/undercount-ci.parity.test.ts -- no copy. ci_low/ci_high "
            "are null when resolved == 0 (no observations to bound)."
        ),
        "cases": cases,
    }


def main() -> None:
    _OUT.write_text(json.dumps(_build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
