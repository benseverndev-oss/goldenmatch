"""The shared-decision inventory."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.report import inventory  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "incident_1c843c8a5"


def test_inventory_reports_the_incident_fields():
    items = inventory(FIXTURES)
    fields = {i["field"] for i in items}
    assert {"passes", "keys"} <= fields, sorted(fields)


def test_every_entry_lists_at_least_two_readers():
    for item in inventory(FIXTURES):
        assert len(item["readers"]) >= 2, item


def test_readers_are_sorted_for_stable_output():
    items = inventory(FIXTURES)
    for item in items:
        assert item["readers"] == sorted(item["readers"]), item
    # Ranking is by accessor count ASCENDING, then field name for ties -- the
    # entries with the fewest accessors (the highest divergence risk; see
    # shared_decisions/report.py's module docstring) lead the list, not
    # universal accessors. Pin the order, not just the per-item sort, so a
    # regression to alphabetical-by-field (or a flip to descending) fails
    # this test rather than merely reading "differently ranked" in a report
    # a human might not re-check.
    counts = [len(i["readers"]) for i in items]
    assert counts == sorted(counts), [i["field"] for i in items]
    keys = [(len(i["readers"]), i["field"]) for i in items]
    assert keys == sorted(keys), [i["field"] for i in items]


def test_allowlisted_fields_are_excluded(monkeypatch):
    import shared_decisions.report as mod

    monkeypatch.setattr(mod, "load_allowlist", lambda: {"passes"})
    fields = {i["field"] for i in mod.inventory(FIXTURES)}
    assert "passes" not in fields
    assert "keys" in fields, "the allowlist removed more than it should"
