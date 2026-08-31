"""The coverage-floor gate must be able to FAIL.

This exists because it could not. A floor named `goldenmatch/core/engine.py`
outlived the module: `check` treated "not in the coverage report" as a
`::warning::` and continued, and `main` then printed
``f"All {len(FLOORS)} module floors met."`` -- so CI reported **15 floors met**
while evaluating 14. The dead entry sat there reporting success for a check that
never ran.

The regression tests below are about the gate's own machinery, not about any
particular coverage number: a dead floor fails, a prefix matching nothing fails,
a real drop fails, and the summary counts what was actually evaluated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from check_coverage_floors import FLOORS, check  # noqa: E402

RATES = {
    "goldenmatch/core/scorer.py": 0.90,
    "goldenmatch/config/schemas.py": 0.90,
    "goldenmatch/config/loader.py": 0.90,
}


def test_floor_on_a_missing_module_fails():
    """The engine.py case: a floor naming something absent is a FAILURE."""
    failures, _ = check(RATES, {"goldenmatch/core/engine.py": 0.75})
    assert len(failures) == 1
    assert "not in the coverage report" in failures[0]


def test_prefix_matching_nothing_fails():
    """A directory floor that matches no module is equally vacuous."""
    failures, checked = check(RATES, {"goldenmatch/nonexistent/": 0.80})
    assert len(failures) == 1
    assert "matches nothing" in failures[0]
    assert checked == 0


def test_real_regression_fails():
    failures, _ = check({"goldenmatch/core/scorer.py": 0.40},
                        {"goldenmatch/core/scorer.py": 0.85})
    assert len(failures) == 1
    assert "actual=40.0% < floor=85.0%" in failures[0]


def test_passing_module_and_prefix_report_no_failure():
    failures, checked = check(
        RATES, {"goldenmatch/core/scorer.py": 0.85, "goldenmatch/config/": 0.85}
    )
    assert failures == []
    assert checked == 2  # one module + one prefix group


def test_checked_count_excludes_dead_entries():
    """`main` prints this number. It must not be len(FLOORS), or a dead floor
    is invisible in the summary line -- the original bug."""
    floors = {
        "goldenmatch/core/scorer.py": 0.85,   # real
        "goldenmatch/core/gone.py": 0.85,     # dead
    }
    failures, checked = check(RATES, floors)
    assert checked == 1, "the dead entry must not be counted as checked"
    assert len(failures) == 1
    assert checked != len(floors)


def test_declared_floors_are_all_plausible():
    """Guard the table itself: floors are fractions, and a DECAY GUARD that
    somehow reached 1.0 would be a typo, not an achievement."""
    for name, floor in FLOORS.items():
        assert 0.0 < floor < 1.0, f"{name}: floor {floor} is not a 0-1 fraction"
        assert name.startswith("goldenmatch/"), f"{name}: floors are repo-relative"
