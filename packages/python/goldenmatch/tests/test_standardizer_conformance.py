"""Runnable conformance for the DIVERGENT standardize boundary (decision 0046).

The 0046 verdict table lists "standardize/transforms" as divergent (not
byte-portable) but named it only in prose -- the one non-exact boundary with no
runnable test (thesis-conformance decision 0047, weakness
`standardize-dates-no-conformance-test`). goldenmatch ships no *date* standardizer
(that divergence is GoldenFlow's, with its own year-guard fixtures); the real,
measurable goldenmatch divergence is the standardizer SET.

This is the Python half of a CHARACTERIZATION: it pins the Python-oracle output so
the divergence cannot silently move on the Python side. The paired TS test
(standardizer-conformance.parity.test.ts) pins the TS side and the agree/diverge
partition. Together they quantify the boundary instead of asserting parity that
does not hold.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core.standardize import get_standardizer

FIXTURE = (Path(__file__).resolve().parents[4]
           / "packages/typescript/goldenmatch/tests/parity/fixtures"
           / "standardizer-conformance.json")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_python_standardizer_output_is_pinned():
    """Every Python standardizer output matches the committed oracle. Regenerate
    with scripts/emit_standardizer_conformance_fixture.py after an intended change."""
    fx = _fixture()
    for name in fx["standardizers"]:
        fn = get_standardizer(name)
        for s in fx["inputs"]:
            assert fn(s) == fx["python"][name][s], (
                f"Python standardizer {name!r} on {s!r} drifted from the oracle; "
                f"re-bless via scripts/emit_standardizer_conformance_fixture.py")


def test_summary_counts_match_the_pinned_blocks():
    """The documented divergence summary must equal what the two pinned blocks
    actually produce -- so the characterization headline can't rot."""
    fx = _fixture()
    agree = diverge = 0
    for name in fx["standardizers"]:
        for s in fx["inputs"]:
            if fx["python"][name][s] == fx["typescript"][name][s]:
                agree += 1
            else:
                diverge += 1
    assert fx["summary"]["agree"] == agree
    assert fx["summary"]["diverge"] == diverge


def test_known_null_vs_empty_divergence_is_present():
    """Pin the dominant, previously-undeclared divergence: Python returns None for
    non-matching input where TS returns "" (email/phone/zip5). If this ever
    converges, the fixture + 0046 amendment must be updated deliberately."""
    fx = _fixture()
    # A non-email input: Python None, TS "".
    bad = "MCDONALD"
    assert fx["python"]["email"][bad] is None
    assert fx["typescript"]["email"][bad] == ""
