"""The shared-decision inventory."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import shared_decisions.allowlist as allowlist_mod  # noqa: E402
from shared_decisions.report import DEFAULT_ROOT, inventory, main  # noqa: E402

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
    # Ranking is by accessor count DESCENDING, then field name ASCENDING for
    # ties -- see shared_decisions/report.py's module docstring: descending
    # was validated against the incident's own position in the real
    # inventory, not argued from first principles. Every field in THIS
    # fixture ties at exactly 2 accessors, so it can only pin the tie-break
    # (field name ascending) and the shape of the count column, not the
    # count direction itself -- test_known_incident_fields_rank_near_the_top
    # below is what actually pins descending, against the real package.
    counts = [len(i["readers"]) for i in items]
    assert counts == sorted(counts, reverse=True), [i["field"] for i in items]
    fields_in_order = [i["field"] for i in items]
    assert fields_in_order == sorted(fields_in_order), fields_in_order


def test_known_incident_fields_rank_near_the_top():
    """Pin the ranking to the EVIDENCE, not to a rule.

    `passes`/`keys` shipped the silent wrong answer this whole detector
    exists to surface (0 pairs where legacy produced 242); `strategy` is the
    same shape (many readers, non-trivial precedence). An ascending-by-count
    ranking buried all three at ranks 64, 67, and 70 of 71 in the real
    inventory -- this test is what makes a future re-sort that buries them
    again fail loudly, naming exactly which field sank.

    THIS TEST IS FRAGILE TO LEGITIMATE GROWTH, ON PURPOSE. As of this
    writing `passes` sits at rank 8 with 11 accessors, TIED with
    `golden_rules` and `threshold` at 11 -- a field gaining or losing one or
    two accessors anywhere in the package could push `passes` to rank 11
    with nothing incident-relevant having changed. If this test starts
    failing: RE-INVESTIGATE whether the ordering still surfaces the
    incident's shape (has something genuinely changed about how widely
    `passes`/`keys`/`strategy` are read?) -- do NOT reflexively widen the
    window to top-15 to make it pass again. A tripwire that people learn to
    silence on sight is worse than no tripwire.
    """
    items = inventory(DEFAULT_ROOT)
    top10 = [item["field"] for item in items[:10]]
    for field in ("keys", "strategy", "passes"):
        assert field in top10, f"{field} fell out of the top 10: {top10}"


def test_allowlisted_fields_are_excluded(monkeypatch):
    import shared_decisions.report as mod

    monkeypatch.setattr(mod, "load_allowlist", lambda: {"passes"})
    fields = {i["field"] for i in mod.inventory(FIXTURES)}
    assert "passes" not in fields
    assert "keys" in fields, "the allowlist removed more than it should"


def _write_allowlist(tmp_path, monkeypatch, lines: list[str]):
    allow = tmp_path / "shared_decisions.allow"
    allow.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(allowlist_mod, "ALLOWLIST", allow)


def test_main_flags_a_stale_allowlist_entry_and_exits_1(tmp_path, monkeypatch, capsys):
    """END-TO-END: drives main() itself, not just inventory()/stale_entries()
    separately. Nothing else in this file calls main() -- with the shipped
    allowlist empty, its only failure-reporting branch (`return 1` for a
    stale entry) has no witness anywhere in the suite without this test."""
    _write_allowlist(tmp_path, monkeypatch, ["not_a_real_field  # never a shared field"])
    rc = main(["--root", str(FIXTURES)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not_a_real_field" in captured.out, captured.out


def test_main_with_no_stale_entries_exits_0(tmp_path, monkeypatch, capsys):
    """Mirror of the above: an allowlist entry that IS still a real shared
    field is not stale, and main() must exit 0 with no STALE line."""
    _write_allowlist(tmp_path, monkeypatch, ["passes  # still shared, agreed"])
    rc = main(["--root", str(FIXTURES)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "STALE" not in captured.out, captured.out


def test_main_json_flags_a_stale_allowlist_entry_and_exits_1(tmp_path, monkeypatch, capsys):
    """The --json branch has its own `1 if stale else 0` line, separate from
    the text branch's -- cover it independently so the two paths can't drift
    apart from each other."""
    _write_allowlist(tmp_path, monkeypatch, ["not_a_real_field  # never a shared field"])
    rc = main(["--root", str(FIXTURES), "--json"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not_a_real_field" in captured.out, captured.out


def test_main_json_with_no_stale_entries_exits_0(tmp_path, monkeypatch, capsys):
    _write_allowlist(tmp_path, monkeypatch, ["passes  # still shared, agreed"])
    rc = main(["--root", str(FIXTURES), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    assert '"stale_allowlist_entries": []' in captured.out, captured.out
