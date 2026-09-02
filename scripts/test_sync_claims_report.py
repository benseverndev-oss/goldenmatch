"""Tests for the sync-claim report."""

from __future__ import annotations

from pathlib import Path

from sync_claims.report import DEFAULT_ROOT, DEFAULT_TESTS, inventory, main

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"


def test_inventory_buckets_the_fixture():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert {c["symbol"] for c in inv["unenforced"]} == {"orphan_lane", "prose_lane"}
    assert {c["symbol"] for c in inv["unverified"]} == {"fast_lane"}
    assert {c["symbol"] for c in inv["unresolvable"]} == {"stray_lane"}


def test_claim_count_and_finding_count_are_separate():
    """Deleting a claim must not read as progress. Reporting only a finding
    count lets six words removed from a docstring look like a fix."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    counts = inv["counts"]
    assert counts["claims"] >= counts["unenforced"]
    assert {
        "claims",
        "resolvable",
        "unenforced",
        "unverified",
        "unresolvable",
        "module_level",
    } <= set(counts)


def test_the_report_names_the_matched_window(capsys):
    """A wrong target resolution must be visible, not silent. The first-match
    rule can pick the wrong symbol when a claim mentions several."""
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "slow_lane" in out
    assert "orphan_lane" in out


def test_the_report_states_its_scope(capsys):
    """Silence outside the scanned tree is not a clean bill, and the header
    has to say so -- module-level claims are reported but never triaged."""
    main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    out = capsys.readouterr().out.lower()
    assert "scope" in out
    assert "module-level" in out


def test_an_empty_tests_root_is_reported_not_presented_as_findings(capsys, tmp_path):
    """If the tests root is wrong every claim looks unenforced. That is a
    broken run, not 100% findings, and the report must say which."""
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NO TEST FILES SCANNED" in out


def test_main_exits_zero_on_findings():
    """C0 is report-only. A finding is not a failure -- the gate is C3."""
    assert main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")]) == 0


def test_the_default_roots_exist():
    """A default path that does not exist makes every CI run vacuously clean."""
    assert DEFAULT_ROOT.is_dir(), DEFAULT_ROOT
    assert DEFAULT_TESTS.is_dir(), DEFAULT_TESTS
