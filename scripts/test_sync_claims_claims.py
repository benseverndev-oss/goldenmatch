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


def test_a_marked_up_target_wins_over_an_earlier_bare_word(tmp_path):
    """The bare word comes FIRST in the window and must still lose.

    This package declares thousands of symbols, many of them ordinary English
    words, so a plain first-match rule resolves "slice one bucket off the
    keyed frame" to `slice`. Measured on the 50 strongest claims during C1
    triage, 7 of 8 sampled targets were wrong that way.
    """
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Byte-identical to the slice path -- see ``score_buckets``."""


def slice():
    pass


def score_buckets():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert len(found) == 1
    assert found[0].target == "score_buckets", (
        f"resolved to {found[0].target!r}; `slice` appears earlier in the window "
        f"but ``score_buckets`` is the one the author marked up as code"
    )


def test_the_bare_word_fallback_still_resolves_when_there_is_no_markup(tmp_path):
    """103 of 216 real claims carry no markup at all -- including this
    phase's motivating incident ("mirrors run_dedupe but returns
    EngineResult"). Dropping the fallback loses the one case the detector
    exists to catch."""
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Mirrors run_dedupe but returns EngineResult."""


def run_dedupe():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert found[0].target == "run_dedupe"


def test_a_sphinx_role_target_is_treated_as_marked_up(tmp_path):
    """`:func:`~mod.name`` is how several goldenmatch docstrings name a target."""
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Mirrors the value path, see :func:`~core.golden.merge_field`."""


def value():
    pass


def merge_field():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert found[0].target == "merge_field"


def test_the_incident_is_HIGH_confidence_despite_a_bare_target():
    """The rule must not exclude the bug the detector exists to catch.

    `_run_pipeline`'s docstring reads "mirrors run_dedupe but returns
    EngineResult" -- `run_dedupe` carries no markup. A high-confidence rule
    that required markup would drop this claim from the triage set, which
    would make the whole confidence split decoration. The bare path exists
    for exactly this case.
    """
    package_symbols = declared_symbols(GOLDENMATCH)
    found = [
        c
        for c in claims(FIXTURE, symbols=package_symbols)
        if c.symbol == "_run_pipeline"
    ]
    assert found[0].target == "run_dedupe"
    assert found[0].confidence == "high", (
        "the motivating incident fell out of the high-confidence set; a gate "
        "that cannot see it is decoration"
    )


def test_a_bare_target_far_from_the_keyword_is_LOW_confidence(tmp_path):
    """The failure C1 measured: a real symbol that the claim does not equate."""
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Mirrors the general shape of the pipeline, and note that the batching
    path is governed elsewhere by helper which does the real work."""


def helper():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert found[0].target == "helper"
    assert found[0].confidence == "low"


def test_a_marked_up_target_is_HIGH_confidence_further_out(tmp_path):
    """Markup is trusted further than a bare word: the author wrote it as code."""
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Byte-identical to the resolved path used by ``helper`` here."""


def helper():
    pass
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert found[0].confidence == "high"


def test_an_unresolved_target_is_LOW_confidence(tmp_path):
    (tmp_path / "m.py").write_text(
        '''
def claimant():
    """Mirrors the legacy behaviour of the old system."""
'''.strip(),
        encoding="utf-8",
    )
    found = [c for c in claims(tmp_path) if c.symbol == "claimant"]
    assert found[0].target is None
    assert found[0].confidence == "low"
