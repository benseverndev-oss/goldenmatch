"""Tests for sync-claim extraction and target resolution."""

from __future__ import annotations

from pathlib import Path

from sync_claims.claims import claims, declared_symbols

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "incident_6c89042c7"
GOLDENMATCH = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def test_the_incident_claim_is_extracted_with_its_target():
    """The motivating example, from a checked-in fixture, not git history.

    `_run_pipeline`'s docstring says "mirrors run_dedupe but returns
    EngineResult". The target is a BARE identifier -- no backticks, no call
    suffix -- which is exactly what an earlier target rule could not see.

    Targets resolve against the REAL package, not the fixture: `run_dedupe`
    lives in `core/pipeline.py`, not in `tui/engine.py`, so resolving against
    the fixture alone returns None and this test could never pass. That is
    what the `symbols` keyword is for.
    """
    package_symbols = declared_symbols(GOLDENMATCH)
    assert "run_dedupe" in package_symbols, (
        "run_dedupe is no longer declared in goldenmatch -- the fixture's claim "
        "names a symbol that has been renamed or removed, so this test's premise "
        "is gone. Fix the premise, do not weaken the assertion."
    )

    found = [c for c in claims(FIXTURE, symbols=package_symbols) if c.symbol == "_run_pipeline"]

    assert len(found) == 1, f"expected one claim on _run_pipeline, got {found}"
    claim = found[0]
    assert claim.keyword.lower() == "mirrors"
    assert claim.target == "run_dedupe", (
        f"target did not resolve to run_dedupe: {claim.target!r}. The claim "
        f"names it as a bare word, so resolution -- not punctuation -- has to "
        f"be the filter."
    )
    assert claim.kind == "symbol"
    assert "run_dedupe" in claim.window


def test_declared_symbols_finds_functions_and_classes():
    symbols = declared_symbols(FIXTURE)
    assert "_run_pipeline" in symbols
    assert "MatchEngine" in symbols


def test_a_docstring_with_no_claim_yields_nothing(tmp_path):
    (tmp_path / "m.py").write_text(
        '''
def plain():
    """Does a thing. Returns a value."""
'''.strip(),
        encoding="utf-8",
    )
    assert claims(tmp_path) == []


def test_an_unresolvable_claim_is_kept_with_target_none(tmp_path):
    """A claim naming nothing real is still a claim -- it is reported in its
    own bucket, not dropped. Dropping it would hide that someone wrote a
    synchronisation promise nobody can check."""
    (tmp_path / "m.py").write_text(
        '''
def widget():
    """Mirrors the legacy behaviour of the old system."""
'''.strip(),
        encoding="utf-8",
    )
    found = claims(tmp_path)
    assert len(found) == 1
    assert found[0].target is None
    assert found[0].keyword.lower() == "mirrors"


def test_a_claim_never_resolves_to_its_own_claimant(tmp_path):
    """`def build(): "mirrors build"` is a self-reference, not a relationship."""
    (tmp_path / "m.py").write_text(
        '''
def build():
    """Mirrors build exactly."""
'''.strip(),
        encoding="utf-8",
    )
    assert claims(tmp_path)[0].target is None


def test_module_level_claims_are_kept_and_marked(tmp_path):
    """Module claims are reported but never triaged -- a module has no single
    symbol a test can reference. Marking the kind is what lets the report
    separate them."""
    (tmp_path / "m.py").write_text(
        '''
"""This module mirrors helper."""


def helper():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = claims(tmp_path)
    assert [c.kind for c in found] == ["module"]
    assert found[0].symbol == "<module>"
    assert found[0].target == "helper"


def test_a_bom_prefixed_file_is_not_skipped(tmp_path):
    """Two goldenmatch modules carry a UTF-8 BOM. Reading as plain utf-8
    raises on the first line and the file vanishes from the scan -- phase B
    lost two modules to exactly this before it was caught."""
    (tmp_path / "bom.py").write_bytes(
        b"\xef\xbb\xbf" + b'def a():\n    """Mirrors b."""\n\n\ndef b():\n    pass\n'
    )
    assert [c.target for c in claims(tmp_path)] == ["b"]
