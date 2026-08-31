"""The universal coverage baseline must fail on all three ways it can go wrong.

Written because the curated floor gate could NOT fail: a floor on a deleted
module printed success for a check that never ran. This gate covers ~11x more
modules, so the same failure here would be ~11x quieter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from check_coverage_baseline import compare  # noqa: E402
from coverage_baseline import (  # noqa: E402
    BASELINE_PATH,
    normalize,
    parse_report,
    tolerance_for,
)

BASE = {
    "goldenmatch/core/scorer.py": {"rate": 0.88, "statements": 500},
    "goldenmatch/core/tiny.py": {"rate": 0.80, "statements": 10},
}


def test_unchanged_passes():
    assert compare(dict(BASE), BASE) == {}


def test_regression_fails():
    measured = {**BASE, "goldenmatch/core/scorer.py": {"rate": 0.60, "statements": 500}}
    problems = compare(measured, BASE)
    assert "regressed" in problems
    assert "scorer.py" in problems["regressed"][0]


def test_new_module_fails():
    """A module nobody wrote a floor for is exactly the 91% this gate is for."""
    measured = {**BASE, "goldenmatch/core/brand_new.py": {"rate": 0.0, "statements": 200}}
    problems = compare(measured, BASE)
    assert "new" in problems
    assert "brand_new.py" in problems["new"][0]
    assert "regressed" not in problems


def test_stale_baseline_entry_fails():
    """The engine.py failure mode: an entry that can no longer be evaluated."""
    measured = {"goldenmatch/core/scorer.py": {"rate": 0.88, "statements": 500}}
    problems = compare(measured, BASE)
    assert "stale" in problems
    assert "tiny.py" in problems["stale"][0]


def test_small_drop_within_tolerance_passes():
    """A gate that fires on shard noise gets muted, which is the worst outcome."""
    measured = {**BASE, "goldenmatch/core/scorer.py": {"rate": 0.87, "statements": 500}}
    assert compare(measured, BASE) == {}


def test_small_module_gets_more_percentage_slack():
    """One line of a 10-statement file is 10pp; the flat 2pp would fire on it."""
    assert tolerance_for(10) > tolerance_for(1000)
    measured = {**BASE, "goldenmatch/core/tiny.py": {"rate": 0.70, "statements": 10}}
    assert compare(measured, BASE) == {}, "one line on a tiny module must not fail"
    # ...but a real collapse on the same module still does
    measured = {**BASE, "goldenmatch/core/tiny.py": {"rate": 0.20, "statements": 10}}
    assert "regressed" in compare(measured, BASE)


def test_normalize_handles_both_report_shapes():
    """CI emits `goldenmatch/core/x.py`; a package-dir report emits `core/x.py`.
    Without normalization every module reads as new when generated the other way."""
    assert normalize("core/scorer.py") == "goldenmatch/core/scorer.py"
    assert normalize("goldenmatch/core/scorer.py") == "goldenmatch/core/scorer.py"
    assert normalize(r"goldenmatch\core\scorer.py") == "goldenmatch/core/scorer.py"


def test_committed_baseline_is_present_and_sane():
    """Guard the artifact itself: an empty or truncated baseline would pass
    everything silently."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    modules = data["modules"]
    assert len(modules) > 300, f"baseline looks truncated: {len(modules)} modules"
    assert data["_meta"]["module_count"] == len(modules)
    for name, info in modules.items():
        assert name.startswith("goldenmatch/"), name
        assert 0.0 <= info["rate"] <= 1.0, (name, info)
        assert info["statements"] >= 0, (name, info)


def test_parse_report_reads_a_real_xml(tmp_path):
    xml = tmp_path / "coverage.xml"
    xml.write_text(
        '<coverage line-rate="0.5"><packages><package><classes>'
        '<class filename="core/x.py" line-rate="0.75">'
        '<lines><line number="1" hits="1"/><line number="2" hits="0"/></lines>'
        "</class></classes></package></packages></coverage>",
        encoding="utf-8",
    )
    got = parse_report(xml)
    assert got == {"goldenmatch/core/x.py": {"rate": 0.75, "statements": 2}}
