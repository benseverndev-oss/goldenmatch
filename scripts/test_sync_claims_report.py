"""Tests for the sync-claim report."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from sync_claims.report import DEFAULT_ROOT, DEFAULT_TESTS, inventory, main

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"


def test_inventory_buckets_the_fixture():
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert {c["symbol"] for c in inv["unenforced"]} == {
        "orphan_lane",
        "prose_lane",
        "arrow_lane",
    }
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


def test_module_level_claims_are_reported_but_never_triaged():
    """A module has no single symbol a test can reference. An earlier
    version of this suite had no module-level claim in the fixture at all,
    so a mutation folding module claims into triage (`resolvable`,
    `unenforced`) passed every test unnoticed."""
    inv = inventory(FIXTURE / "src", FIXTURE / "tests")
    assert inv["counts"]["module_level"] == 1
    assert len(inv["module_level"]) == 1
    entry = inv["module_level"][0]
    assert entry["symbol"] == "<module>"
    assert entry["target"] == "slow_lane"
    assert "<module>" not in {c["symbol"] for c in inv["unenforced"]}
    assert "<module>" not in {c["symbol"] for c in inv["unverified"]}
    assert "<module>" not in {c["symbol"] for c in inv["unresolvable"]}


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
    # Substance, not just presence: a co-referenced claim must be described
    # as UNSAFE, never as verified or enforced. A mutation that flipped this
    # to "is safe and enforced" passed every earlier test in this file.
    assert "unverified is not safe" in out


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


def test_main_survives_non_utf8_stdout(monkeypatch):
    """main() is contracted to exit 0 whatever it finds. `arrow_lane`'s claim
    window carries a real non-ASCII character (an arrow) on purpose, and the
    fixed stdout below uses cp1252 with strict errors -- the codepage this
    broke on for real, unpatched by any PYTHONIOENCODING invocation-side
    workaround. Without the guarded `sys.stdout.reconfigure` in `main`, this
    raises UnicodeEncodeError partway through the findings loop: the process
    exits non-zero after printing only a few findings, which reads as a
    short complete report rather than the truncated one it actually is."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    rc = main(["--root", str(FIXTURE / "src"), "--tests", str(FIXTURE / "tests")])
    stream.flush()
    out = buf.getvalue().decode("cp1252")
    assert rc == 0
    assert "arrow_lane" in out


def test_the_default_roots_exist():
    """A default path that does not exist makes every CI run vacuously clean."""
    assert DEFAULT_ROOT.is_dir(), DEFAULT_ROOT
    assert DEFAULT_TESTS.is_dir(), DEFAULT_TESTS
