"""Tests for the executable-reference enforcement check."""

from __future__ import annotations

from pathlib import Path

from sync_claims.claims import claims, declared_symbols
from sync_claims.enforcement import (
    executable_references,
    test_reference_sets,
    unenforced,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "sync_enforcement"
SRC = FIXTURE / "src"
TESTS = FIXTURE / "tests"


def _claims():
    return claims(SRC, symbols=declared_symbols(SRC))


def _symbol_claims():
    """Symbol-level claims only, scoped the way `inventory()` scopes its
    input to `unenforced()`. `unenforced()` itself only filters on
    `target is None`, not on `kind` -- a module-level claim with a
    resolved target (the fixture now has one) would otherwise flow
    straight into a finding set that is supposed to hold only claims a
    test COULD reference both halves of."""
    return [c for c in _claims() if c.kind == "symbol"]


def test_a_docstring_only_co_mention_is_not_enforcement():
    """THE TRAP THIS PHASE TURNS ON.

    At 6c89042c7^, tests/test_engine.py named both `_run_pipeline` and
    `run_dedupe` -- inside a docstring. A text scan calls that enforced, and
    the phase misses the bug that motivates it.
    """
    names = executable_references(TESTS / "test_docstring_only.py")
    assert "prose_lane" in names
    assert "slow_lane" not in names, (
        "slow_lane appears only in a docstring; counting it means counting prose as enforcement"
    )


def test_the_unenforced_claims_are_reported():
    found = {c.symbol for c in unenforced(_symbol_claims(), test_reference_sets(TESTS))}
    assert found == {"orphan_lane", "prose_lane", "arrow_lane"}, found


def test_the_enforced_claim_is_not_reported():
    found = {c.symbol for c in unenforced(_claims(), test_reference_sets(TESTS))}
    assert "fast_lane" not in found, (
        "test_enforced.py references both fast_lane and slow_lane in code"
    )


def test_a_claim_with_no_target_is_never_reported_unenforced():
    """An unresolvable claim has nothing to be enforced against. Reporting it
    as unenforced would inflate the finding count with claims nobody can act on.

    `stray_lane` exists in the fixture to give this something to assert on. An
    earlier draft filtered for `target is None` against a fixture that had no
    such claim, so it iterated an empty list and passed while checking nothing.
    """
    unresolved = [c for c in _claims() if c.target is None]
    assert {c.symbol for c in unresolved} == {"stray_lane"}, (
        "the fixture must contain an unresolvable claim or this test is vacuous"
    )
    assert unenforced(unresolved, []) == []


def test_executable_references_covers_all_three_node_kinds():
    """Name, Attribute and alias. Missing `alias` loses every symbol a test
    only imports, which is most of them."""
    path = TESTS / "test_enforced.py"
    names = executable_references(path)
    assert {"fast_lane", "slow_lane"} <= names


def test_an_empty_tests_directory_yields_no_reference_sets(tmp_path):
    """An empty list is how 'nothing was scanned' reaches the report.

    Distinguishing that from 'nothing is enforced' is the report's job
    (test_sync_claims_report.py); this pins the signal it keys on."""
    assert test_reference_sets(tmp_path) == []
