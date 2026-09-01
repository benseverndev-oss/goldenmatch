"""The intersection, and the guarantees that make it safe to act on.

These tests drive a SYNTHETIC coverage.xml. An earlier draft asserted over
`candidates(None)`, which returns [] by design -- so every assertion passed over
an empty set and tested nothing. That is the precise defect this whole plan
exists to prevent, and it nearly shipped inside the plan itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.allowlist import load_allowlist  # noqa: E402
from dead_code.liveness import live_modules  # noqa: E402
from dead_code.report import candidates  # noqa: E402
from dead_code.static import unimported_modules  # noqa: E402


def _pick_real_candidate() -> str:
    """A module that IS statically unimported, live-free and un-allowlisted.

    Chosen dynamically rather than hardcoded: pinning a specific module name
    would break the day someone imports it, and the test would then be
    'fixed' by weakening it.
    """
    pool = sorted(unimported_modules() - live_modules() - load_allowlist())
    if not pool:
        pytest.skip("no unimported module available to build a fixture from")
    return pool[0]


def _coverage_xml(tmp_path: Path, uncovered: list[str], covered: list[str]) -> Path:
    """Minimal coverage.xml in the shape report._uncovered_modules parses."""
    lines = ['<?xml version="1.0" ?>', "<coverage><packages><package><classes>"]
    for mod in uncovered:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append('<lines><line number="1" hits="0"/></lines></class>')
    for mod in covered:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append('<lines><line number="1" hits="3"/></lines></class>')
    lines.append("</classes></package></packages></coverage>")
    p = tmp_path / "coverage.xml"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_a_module_failing_both_signals_is_reported(tmp_path):
    """The fixture must actually produce a candidate -- otherwise every
    assertion below is vacuous."""
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[target], covered=[])
    assert {c["module"] for c in candidates(xml)} == {target}


def test_a_registry_live_module_is_never_reported(tmp_path):
    """The whole point of the inversion. Feed the report a live module that
    IS in the static candidate pool, with zero coverage: it must still not be
    a candidate.

    The victim must come from live_modules() & unimported_modules(), not from
    live_modules() alone -- a live module that was never in the static pool
    to begin with would pass this assertion whether or not the `- live`
    exclusion exists, witnessing nothing.
    """
    overlap = sorted(live_modules() & unimported_modules())
    if not overlap:
        pytest.fail(
            "no module is both registry-live and statically unimported -- "
            "this test can no longer witness the liveness exclusion"
        )
    victim = overlap[0]
    xml = _coverage_xml(tmp_path, uncovered=[victim], covered=[])
    assert victim not in {c["module"] for c in candidates(xml)}


def test_an_allowlisted_module_is_never_reported(tmp_path):
    allowed = sorted(load_allowlist())
    xml = _coverage_xml(tmp_path, uncovered=allowed, covered=[])
    assert not {c["module"] for c in candidates(xml)} & set(allowed)


def test_a_covered_module_is_never_reported(tmp_path):
    """Runtime execution alone is enough to clear a module, whatever the
    static signal says."""
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[], covered=[target])
    assert target not in {c["module"] for c in candidates(xml)}


def test_every_candidate_carries_its_evidence(tmp_path):
    target = _pick_real_candidate()
    xml = _coverage_xml(tmp_path, uncovered=[target], covered=[])
    for c in candidates(xml):
        assert c["static"] is True
        assert c["runtime"] is True


def test_without_coverage_runtime_evidence_is_absent_not_assumed():
    """With no coverage.xml the runtime signal is unknown, so NOTHING is a
    candidate. An unknown treated as proof is how live code gets deleted."""
    assert candidates(None) == []
