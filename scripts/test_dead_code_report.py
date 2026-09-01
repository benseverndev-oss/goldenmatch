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
from dead_code.report import candidacy_scope, candidates  # noqa: E402
from dead_code.static import unimported_modules  # noqa: E402


def _pick_real_candidate() -> str:
    """A module that IS statically unimported, live-free and un-allowlisted.

    Chosen dynamically rather than hardcoded: pinning a specific module name
    would break the day someone imports it, and the test would then be
    'fixed' by weakening it.

    Scoped to goldenmatch: the real coverage.xml this detector consumes is
    produced with `source = ["goldenmatch"]` (see .github/workflows/ci.yml),
    so it can never contain a class from any other package, and
    coverage_paths.normalize() (used by _uncovered_modules) is itself
    goldenmatch-only -- it prepends "goldenmatch/" to anything that isn't
    already rooted there. A victim from another package would get the wrong
    prefix silently prepended by normalize() and candidates(xml) would come
    back empty, which every "not reported" assertion below would then pass
    vacuously instead of actually witnessing the exclusion it names.
    """
    pool = sorted(
        m
        for m in (unimported_modules() - live_modules() - load_allowlist())
        if m.startswith("goldenmatch.")
    )
    if not pool:
        pytest.skip("no unimported goldenmatch module available to build a fixture from")
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


def _ci_shape_coverage_xml(tmp_path: Path, uncovered: list[str]) -> Path:
    """coverage.xml using the REAL CI filename shape, not the clean one.

    CI's `coverage xml` emits repo-root-relative names with the doubled
    `packages/python/goldenmatch/goldenmatch/` nesting (see
    scripts/coverage_paths.py's docstring), not the package-relative
    `goldenmatch/...` shape `_coverage_xml` above hand-writes. A fixture that
    only ever exercises the clean shape can pass while the real pipeline is
    broken -- which is exactly what shipped in the first version of this job.
    """
    lines = ['<?xml version="1.0" ?>', "<coverage><packages><package><classes>"]
    for mod in uncovered:
        rel = mod.replace(".", "/")
        lines.append('<class filename="packages/python/goldenmatch/' + rel + '.py">')
        lines.append('<lines><line number="1" hits="0"/></lines></class>')
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

    Scoped to goldenmatch, same reasoning as _pick_real_candidate(): the
    runtime signal (and therefore candidates()) only ever exists for
    goldenmatch modules, so a non-goldenmatch victim can never appear in
    _uncovered_modules()'s output regardless of whether the `- live`
    exclusion is even present -- the assertion would pass either way and
    witness nothing. This is the same defect fixed in this test in an
    earlier round, reintroduced through coverage_paths.normalize()'s
    goldenmatch-only construction.
    """
    overlap = sorted(
        m for m in (live_modules() & unimported_modules()) if m.startswith("goldenmatch.")
    )
    if not overlap:
        pytest.fail(
            "no goldenmatch module is both registry-live and statically unimported -- "
            "this test can no longer witness the liveness exclusion (the victim must "
            "also be goldenmatch-scoped, since only a goldenmatch module can ever have "
            "a runtime signal for the exclusion to apply to)"
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


def test_real_ci_filename_shape_is_recognized(tmp_path):
    """CI's actual filename shape (repo-root-relative, doubled
    goldenmatch/goldenmatch/ nesting) must produce a candidate, not just the
    hand-simplified goldenmatch/... shape the other fixtures in this file
    use. Reproduces the bug where _uncovered_modules compared this shape
    naively against unimported_modules()'s canonical names and the
    intersection was always empty -- the report found nothing, forever, on
    every real CI run."""
    target = _pick_real_candidate()
    xml = _ci_shape_coverage_xml(tmp_path, uncovered=[target])
    assert {c["module"] for c in candidates(xml)} == {target}


def test_non_goldenmatch_module_is_excluded_not_evaluated(tmp_path):
    """A module outside goldenmatch can never be a candidate -- not because
    it's clean, but because the combined coverage.xml is goldenmatch's alone
    (`source = ["goldenmatch"]`) and coverage_paths.normalize() is itself
    goldenmatch-only, so no other package ever has a runtime signal at all.

    Pins BOTH halves of that distinction: the module is never reported, AND
    candidacy_scope()'s excluded count is non-zero -- so if coverage.xml ever
    becomes multi-package, this test fails and tells whoever changed it to
    revisit the goldenmatch-only restriction rather than letting the report's
    meaning silently change from "out of scope" to "evaluated and clean".
    """
    pool = sorted(
        m
        for m in (unimported_modules() - live_modules() - load_allowlist())
        if not m.startswith("goldenmatch.")
    )
    if not pool:
        pytest.fail(
            "no non-goldenmatch unimported module exists -- this test can no "
            "longer witness the goldenmatch-only runtime-signal restriction"
        )
    victim = pool[0]
    xml = _coverage_xml(tmp_path, uncovered=[victim], covered=[])
    assert victim not in {c["module"] for c in candidates(xml)}

    scope = candidacy_scope()
    assert scope["excluded_no_runtime_signal"] > 0, (
        "excluded count is zero -- either every unimported module is now "
        "goldenmatch-scoped, or candidacy_scope() stopped counting correctly. "
        "Either way this test can no longer witness the restriction it names."
    )
