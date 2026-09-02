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


def _pick_two_real_candidates() -> tuple[str, str]:
    """Two distinct modules from the same eligible pool _pick_real_candidate() draws
    from, for tests that need to tell a line-less victim apart from a covered-with-
    lines-all-zero victim in the same fixture."""
    pool = sorted(
        m
        for m in (unimported_modules() - live_modules() - load_allowlist())
        if m.startswith("goldenmatch.")
    )
    if len(pool) < 2:
        pytest.skip("fewer than two unimported goldenmatch modules available")
    return pool[0], pool[1]


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


def _lineless_coverage_xml(
    tmp_path: Path, lineless: list[str], uncovered_with_lines: list[str] | None = None
) -> Path:
    """coverage.xml where `lineless` modules get a <class> with NO <line> children.

    Reproduces the real shape a bare `__init__.py` or a docstring-only module
    gets from `coverage xml`: the file is still collected as a `<class>` (it's
    part of `source = ["goldenmatch"]`), it just has no `<line>` children because
    there was nothing in it for coverage.py to measure. This is the shape that
    made 14 of 14 candidates in the first real CI run false positives -- every
    one was a package `__init__.py` or docstring-only module read as "covered
    zero lines" when it had zero MEASURABLE lines.

    `uncovered_with_lines` optionally adds real zero-hit modules alongside, so a
    single fixture can assert both behaviors don't interfere with each other.
    """
    lines = ['<?xml version="1.0" ?>', "<coverage><packages><package><classes>"]
    for mod in lineless:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append("<lines></lines></class>")
    for mod in uncovered_with_lines or []:
        lines.append(f'<class filename="{mod.replace(".", "/")}.py">')
        lines.append('<lines><line number="1" hits="0"/></lines></class>')
    lines.append("</classes></package></packages></coverage>")
    p = tmp_path / "coverage.xml"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_a_lineless_module_is_not_reported(tmp_path):
    """A <class> with NO <line> children at all -- the shape a bare __init__.py
    or a docstring-only module gets from `coverage xml` -- must NOT be reported,
    even though it is in the static pool and neither live nor allowlisted.

    Before the fix, `_uncovered_modules` read "zero lines with hits > 0" as
    uncovered regardless of whether there was anything to hit, so this exact
    fixture reported the victim as a candidate. That was the real bug: every
    one of the first CI run's 14 candidates was a module in this shape.
    """
    target = _pick_real_candidate()
    xml = _lineless_coverage_xml(tmp_path, lineless=[target])
    assert target not in {c["module"] for c in candidates(xml)}


def test_an_uncovered_module_with_real_lines_is_still_reported(tmp_path):
    """The line-less guard must not over-suppress: a module WITH measurable
    lines, all at hits="0", is the genuine uncovered case and must still be
    reported -- alongside a line-less module in the same fixture, so the guard
    can't be satisfied by some accident that suppresses both."""
    lineless_victim, uncovered_victim = _pick_two_real_candidates()
    xml = _lineless_coverage_xml(
        tmp_path, lineless=[lineless_victim], uncovered_with_lines=[uncovered_victim]
    )
    found = {c["module"] for c in candidates(xml)}
    assert uncovered_victim in found
    assert lineless_victim not in found


def test_lineless_exclusion_count_is_surfaced_and_nonzero(tmp_path):
    """candidacy_scope(coverage_xml) must disclose how many modules were set
    aside for having no measurable lines -- the same disclosure habit this
    report already applies to the goldenmatch-only restriction. A fixture with
    a known line-less module must make that count nonzero, or the disclosure
    itself is decorative."""
    target = _pick_real_candidate()
    xml = _lineless_coverage_xml(tmp_path, lineless=[target])
    scope = candidacy_scope(xml)
    assert scope["excluded_no_measurable_lines"] > 0


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


def test_candidate_schema_has_static_and_runtime_keys(tmp_path):
    """Every candidate dict carries `static` and `runtime` keys, both True.

    This checks the schema is present and well-formed, not that the values
    were independently gathered per candidate -- they can't be, and aren't:
    `candidates()` sets them as hardcoded literals, True BY CONSTRUCTION,
    because a module only becomes a candidate when both signals already
    agree (see the comment at that construction site). A candidate with
    `static: False` or `runtime: False` would be a contradiction of how the
    list was built, not a finding this test could discover either way.
    """
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

    Two assertions below, but only the SECOND one witnesses that claim. The
    first (`victim not in candidates(...)`) is vacuous: _coverage_xml writes
    victim's bare dotted-to-slash filename (e.g. "goldenflow/foo.py"), and
    normalize() -- finding no "goldenmatch/" substring in it -- rewrites that
    to "goldenmatch/goldenflow/foo.py". That mangled name matches nothing in
    the static pool regardless of whether the goldenmatch-only eligibility
    filter exists at all, so the assertion would pass even if
    `_goldenmatch_eligible` were deleted. It stays because a regression that
    makes it FAIL would still be worth catching; it just cannot serve as
    evidence that the restriction holds. Only the second assertion
    (`candidacy_scope()["excluded_no_runtime_signal"] > 0`) actually pins the
    restriction -- so if coverage.xml ever becomes multi-package, that count
    goes to zero and tells whoever changed it to revisit the goldenmatch-only
    restriction rather than letting the report's meaning silently change from
    "out of scope" to "evaluated and clean".
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
