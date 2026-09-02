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
    found = {c.symbol for c in unenforced(_symbol_claims(), test_reference_sets(TESTS))}
    assert "fast_lane" not in found, (
        "test_enforced.py references both fast_lane and slow_lane in code"
    )


def test_a_resolvable_module_level_claim_is_never_reported_unenforced():
    """`unenforced()` now filters `kind != "symbol"` itself, not just
    `inventory()`'s pre-scoping of its input. The fixture's module-level
    claim ("this module mirrors slow_lane") has a RESOLVABLE target
    (`slow_lane` is a declared symbol), so without the kind filter it would
    slip past the `target is None` check and become a permanent finding: no
    test can ever reference the literal string "<module>" in code, because
    it names no real symbol."""
    module_claims = [c for c in _claims() if c.kind == "module"]
    assert {c.symbol for c in module_claims} == {"<module>"}
    assert all(c.target is not None for c in module_claims), (
        "the fixture must contain a RESOLVABLE module-level claim or this test is vacuous"
    )
    found = unenforced(module_claims, test_reference_sets(TESTS))
    assert found == []


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


def test_the_incident_claim_is_classified_unenforced_against_the_real_package():
    """C0's exit criterion is BOTH halves: the detector must extract
    `_run_pipeline --mirrors--> run_dedupe` from the checked-in fixture AND
    classify it unenforced. `test_sync_claims_claims.py` pins the extraction;
    every enforcement test above this one runs only the synthetic
    `sync_enforcement` fixture. Nothing else runs the whole path -- extract,
    resolve against the real package, scan the real test tree, classify --
    on the incident that motivates this phase. That is the branch's headline
    promise, and until this test existed nothing pinned it."""
    incident_fixture = REPO / "scripts" / "fixtures" / "incident_6c89042c7"
    goldenmatch = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"
    goldenmatch_tests = REPO / "packages" / "python" / "goldenmatch" / "tests"

    package_symbols = declared_symbols(goldenmatch)
    incident_claims = [
        c for c in claims(incident_fixture, symbols=package_symbols) if c.symbol == "_run_pipeline"
    ]
    assert len(incident_claims) == 1, f"expected one claim on _run_pipeline, got {incident_claims}"
    claim = incident_claims[0]
    assert claim.target == "run_dedupe"

    reference_sets = test_reference_sets(goldenmatch_tests)
    findings = unenforced(incident_claims, reference_sets)
    assert claim in findings, (
        "the incident claim (_run_pipeline --mirrors--> run_dedupe) must come "
        "back as a finding when run against the real goldenmatch test tree"
    )
