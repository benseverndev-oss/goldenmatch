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


def test_every_entry_lists_at_least_two_accessors():
    for item in inventory(FIXTURES):
        assert len(item["accessors"]) >= 2, item


def test_accessors_are_sorted_for_stable_output():
    items = inventory(FIXTURES)
    for item in items:
        assert item["accessors"] == sorted(item["accessors"]), item
    # Ranking is by accessor count DESCENDING, then field name ASCENDING for
    # ties -- see shared_decisions/report.py's module docstring: descending
    # was validated against the incident's own position in the real
    # inventory, not argued from first principles. Every field in THIS
    # fixture ties at exactly 2 accessors, so it can only pin the tie-break
    # (field name ascending) and the shape of the count column, not the
    # count direction itself -- test_known_incident_fields_rank_near_the_top
    # below is what actually pins descending, against the real package.
    counts = [len(i["accessors"]) for i in items]
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
    for field in ("keys", "strategy"):
        assert field in top10, f"{field} fell out of the top 10: {top10}"

    # `passes` USED to be here, and left for the right reason: PR #2845 moved
    # the keys-vs-passes decision onto `BlockingConfig.resolved_keys()` and
    # routed ten call sites through it, so four modules stopped reading
    # `.passes` directly and its accessor count fell. The window was NOT
    # widened to top-15 to paper over that -- this docstring's own warning
    # forbids it. What the tripwire still guarantees is that the field remains
    # VISIBLE: it must not vanish from the inventory altogether while
    # `core/blocker.py` and five others still read it.
    fields = [item["field"] for item in items]
    assert "passes" in fields, (
        "`passes` disappeared from the inventory entirely. That is not the "
        "remediation shrinking its accessor count -- it means the scan stopped "
        "seeing a field the 1c843c8a5 incident turned on. Investigate the "
        "accessor rule before touching this test."
    )


def test_a_field_declared_on_multiple_classes_carries_the_ambiguity_marker():
    """`readers.py` keys purely by field NAME -- it has no type information,
    so `rule.strategy` (BlockingConfig, GoldenFieldRule, GoldenGroupRule all
    declare a `strategy` field) is pooled as if every accessor referred to
    the SAME field. Full disambiguation needs type inference and is out of
    scope; this pins that the report at least surfaces the ambiguity rather
    than silently implying "these N modules must agree" about fields that
    may share nothing but a name."""
    items = inventory(DEFAULT_ROOT)
    strategy = next(i for i in items if i["field"] == "strategy")
    assert set(strategy["declared_on"]) == {
        "BlockingConfig",
        "GoldenFieldRule",
        "GoldenGroupRule",
    }, strategy["declared_on"]


def test_main_prints_the_declared_on_marker_for_a_multi_class_field(capsys):
    """The marker has to reach the actual triaged text, not just the return
    value -- a human reads main()'s stdout, not inventory()'s list[dict]."""
    rc = main(["--root", str(DEFAULT_ROOT)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "strategy" in out
    assert "DECLARED ON 3 CLASSES" in out, out
    assert "BlockingConfig" in out and "GoldenFieldRule" in out and "GoldenGroupRule" in out


def test_a_single_class_field_carries_no_marker(capsys):
    """Contrast case: `passes` is declared on exactly one class
    (`BlockingConfig`) in the fixture, so its line in the report must not
    carry the ambiguity marker."""
    items = inventory(FIXTURES)
    passes = next(i for i in items if i["field"] == "passes")
    assert passes["declared_on"] == ["BlockingConfig"], passes["declared_on"]

    rc = main(["--root", str(FIXTURES)])
    out = capsys.readouterr().out
    assert rc == 0
    passes_line = next(line for line in out.splitlines() if line.strip().startswith("passes "))
    assert "DECLARED ON" not in passes_line, passes_line


def test_unparseable_modules_are_counted_in_the_text_report(monkeypatch, capsys):
    """A BOM (or any other future parse failure) leaves a module invisible
    to every count above it -- readers.py used to skip and say nothing,
    the same silence `modules_without_coverage_data` exists to surface in
    the companion parity_coverage.py tool, applied inconsistently. The
    report must say so."""
    import shared_decisions.report as mod

    monkeypatch.setattr(mod, "unparseable_modules", lambda root: ["weird/bommed.py"])
    rc = main(["--root", str(FIXTURES)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 module(s) could not be parsed" in out, out
    assert "weird/bommed.py" in out, out


def test_unparseable_modules_are_counted_in_the_json_report(monkeypatch, capsys):
    import shared_decisions.report as mod

    monkeypatch.setattr(mod, "unparseable_modules", lambda root: ["weird/bommed.py"])
    rc = main(["--root", str(FIXTURES), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"unparseable_modules": [\n    "weird/bommed.py"\n  ]' in out, out


def test_report_header_states_scan_scope(capsys):
    """`fields.py` reads only config/schemas.py (web/settings.py's BaseModels
    are invisible) and DEFAULT_ROOT is goldenmatch only (scripts/, goldenflow,
    the TypeScript port are out of reach by construction). A reader must not
    mistake either silence for a clean bill -- the header has to say so."""
    rc = main(["--root", str(FIXTURES)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "scope" in out.lower()
    assert "web/settings.py" in out
    assert str(FIXTURES) in out


def test_shared_fields_is_computed_once_per_invocation(monkeypatch, capsys):
    """`report.py` used to call `shared_fields(root)` twice per run -- once
    inside `inventory()`, once again for the stale-allowlist check -- each
    re-parsing all ~493 files under root. It's a pure function so the two
    calls never disagreed, it was just double the CI cost. Pin the call
    count so a future edit can't reintroduce the duplicate parse."""
    import shared_decisions.report as mod

    calls = []
    real = mod.shared_fields

    def counting(root):
        calls.append(root)
        return real(root)

    monkeypatch.setattr(mod, "shared_fields", counting)
    rc = main(["--root", str(FIXTURES)])
    assert rc == 0
    assert len(calls) == 1, f"shared_fields(root) called {len(calls)} times, expected 1"


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
