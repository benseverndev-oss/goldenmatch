"""Tests for the generic job-vs-filter coverage check in check_filter_coverage.py.

The curated REQUIRED/FORBIDDEN tables in that module are populated one incident
at a time and never inspect what a job actually runs -- which is exactly why
they could not have caught #2839 (`dead_code` / `goldenmatch_sweep_coverage`
gated on `python_goldenmatch`, a filter that never watches `scripts/`, where
the detector those jobs run actually lives). `check_job_filter_coverage()` is
the generic version: for every job, does its own gating filter set cover
every path its `run:` steps actually touch.

The sabotage test at the bottom is the load-bearing one: it reverts THIS
branch's fix in a scratch copy of ci.yml and asserts the check reports
`dead_code` again. A guard that cannot reproduce the incident it was written
for is worthless -- see the module docstring in check_filter_coverage.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

import check_filter_coverage as cfc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Two real, small, on-disk files used as synthetic "the job runs this" evidence.
# `_extract_run_paths` only keeps tokens that exist on disk (by design -- a
# nonexistent path is noise, not evidence), so a synthetic fixture has to
# reference something real rather than an invented path.
REAL_PATH_UNDER_SCRIPTS = "scripts/check_filter_coverage.py"
REAL_PATH_UNDER_PACKAGES = "packages/python/goldenmatch/pyproject.toml"


def _write_synthetic(tmp_path: Path, *, job_if: str, run_text: str, filter_patterns: list[str]):
    """A minimal two-file (ci.yml, filters.yml) pair: one `changes` job whose
    `outputs:` maps 1:1 to a single paths-filter key `pkg_filter`, and one
    `myjob` gated on it via `needs.changes.outputs.pkg_filter`."""
    ci_spec = {
        "jobs": {
            "changes": {
                "outputs": {"pkg_filter": "${{ steps.filter.outputs.pkg_filter }}"},
            },
            "myjob": {
                "if": job_if,
                "steps": [{"run": run_text}],
            },
        }
    }
    ci_path = tmp_path / "ci.yml"
    ci_path.write_text(yaml.safe_dump(ci_spec, sort_keys=False), encoding="utf-8")

    filters_path = tmp_path / "filters.yml"
    filters_path.write_text(
        yaml.safe_dump({"pkg_filter": filter_patterns}, sort_keys=False), encoding="utf-8"
    )
    return ci_path, filters_path


def test_uncovered_path_is_reported(tmp_path: Path):
    """myjob runs a scripts/ path; its only gating filter covers packages/**."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="needs.changes.outputs.pkg_filter == 'true'",
        run_text=f"uv run python {REAL_PATH_UNDER_SCRIPTS}",
        filter_patterns=["packages/**"],
    )
    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert ("myjob", REAL_PATH_UNDER_SCRIPTS) in violations


def test_covered_path_is_not_reported(tmp_path: Path):
    """Same job, but the gating filter's pattern actually covers the path."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="needs.changes.outputs.pkg_filter == 'true'",
        run_text=f"uv run python {REAL_PATH_UNDER_SCRIPTS}",
        filter_patterns=["scripts/**"],
    )
    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_job_without_if_is_not_reported(tmp_path: Path):
    """A job with no `if:` runs unconditionally -- it makes no coverage claim,
    so an uncovered-looking path in it is not a violation."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="",  # placeholder; overwritten below by dropping the key entirely
        run_text=f"uv run python {REAL_PATH_UNDER_SCRIPTS}",
        filter_patterns=["packages/**"],
    )
    ci_spec = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    del ci_spec["jobs"]["myjob"]["if"]
    ci_path.write_text(yaml.safe_dump(ci_spec, sort_keys=False), encoding="utf-8")

    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_force_all_alone_is_not_a_coverage_claim(tmp_path: Path):
    """`force_all` is an override ("run anyway"), not a coverage claim -- a job
    gated ONLY on it (after excluding force_all, no filter names remain) makes
    no assertion this check can verify, same as having no `if:` at all."""
    ci_spec = {
        "jobs": {
            "changes": {"outputs": {"force_all": "${{ steps.flags.outputs.force_all }}"}},
            "myjob": {
                "if": "needs.changes.outputs.force_all == 'true'",
                "steps": [{"run": f"uv run python {REAL_PATH_UNDER_SCRIPTS}"}],
            },
        }
    }
    ci_path = tmp_path / "ci.yml"
    ci_path.write_text(yaml.safe_dump(ci_spec, sort_keys=False), encoding="utf-8")
    filters_path = tmp_path / "filters.yml"
    filters_path.write_text(yaml.safe_dump({}, sort_keys=False), encoding="utf-8")

    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_comment_only_path_is_not_reported(tmp_path: Path):
    """A path mentioned only in a `#`-comment inside `run:` is not evidence the
    job executes or reads it."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="needs.changes.outputs.pkg_filter == 'true'",
        run_text=f"# see {REAL_PATH_UNDER_SCRIPTS} for context\necho hi",
        filter_patterns=["packages/**"],
    )
    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_nonexistent_path_is_not_reported(tmp_path: Path):
    """A path-shaped token that does not exist on disk is noise, not evidence."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="needs.changes.outputs.pkg_filter == 'true'",
        run_text="uv run python scripts/this_file_does_not_exist_anywhere.py",
        filter_patterns=["packages/**"],
    )
    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_directory_reference_matches_like_dorny_would(tmp_path: Path):
    """dorny/paths-filter only ever evaluates changed FILES, never bare
    directories -- `cd packages/python/goldenmatch` naming a directory the
    filter's own `<dir>/**` pattern covers must not be reported (see
    `_covered`'s docstring, validated there against the real
    `goldenflow_nopolars` job/filter pair in ci.yml)."""
    ci_path, filters_path = _write_synthetic(
        tmp_path,
        job_if="needs.changes.outputs.pkg_filter == 'true'",
        run_text="cd packages/python/goldenmatch && uv run pytest",
        filter_patterns=["packages/python/goldenmatch/**"],
    )
    violations = cfc.check_job_filter_coverage(ci_path=ci_path, filters_path=filters_path)
    assert violations == set()


def test_real_ci_yields_only_known_violations():
    """The real ci.yml/filters.yml pair must not produce any (job, path)
    violation outside the ratcheted KNOWN_JOB_FILTER_GAPS floor."""
    found = cfc.check_job_filter_coverage()
    new = found - set(cfc.KNOWN_JOB_FILTER_GAPS)
    assert not new, f"NEW job-filter gap(s) found, not yet in KNOWN_JOB_FILTER_GAPS: {sorted(new)}"


def test_dead_code_and_sweep_coverage_have_no_known_gaps():
    """Hard requirement for this branch: the filter fix for #2839 means neither
    `dead_code` nor `goldenmatch_sweep_coverage` may appear in the ratchet.
    If either does, the filter fix is incomplete."""
    known_jobs = {job for job, _path in cfc.KNOWN_JOB_FILTER_GAPS}
    assert "dead_code" not in known_jobs
    assert "goldenmatch_sweep_coverage" not in known_jobs

    found = cfc.check_job_filter_coverage()
    found_jobs = {job for job, _path in found}
    assert "dead_code" not in found_jobs
    assert "goldenmatch_sweep_coverage" not in found_jobs


def test_sabotage_reverted_dead_code_wiring_is_caught(tmp_path: Path):
    """SABOTAGE CHECK. In a scratch copy of the real ci.yml, revert this
    branch's fix -- strip the `needs.changes.outputs.dead_code == 'true' ||`
    clause back out of the `dead_code` job's `if:`, so it is gated on
    `python_goldenmatch` alone again, exactly as it was before #2839's fix.
    The `dead_code` filter itself (in the real, unmodified filters.yml) still
    lists `scripts/dead_code/**` etc. -- but the job no longer gates on it, so
    the check must report every scripts/ path the job runs as uncovered. This
    is the exact incident the whole check exists to catch; if this test does
    not fail before the fix (and pass after), the guard is worthless.
    """
    real_ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    ci_spec = yaml.safe_load(real_ci_path.read_text(encoding="utf-8"))

    job = ci_spec["jobs"]["dead_code"]
    before = job["if"]
    reverted = before.replace("needs.changes.outputs.dead_code == 'true' || ", "")
    assert reverted != before, (
        "the dead_code job's if: no longer contains the expected clause -- "
        "update this test's string to match the current wiring"
    )
    job["if"] = reverted

    scratch_ci = tmp_path / "ci_sabotage.yml"
    scratch_ci.write_text(yaml.safe_dump(ci_spec, sort_keys=False), encoding="utf-8")

    # filters.yml is untouched -- only the job's wiring was reverted, exactly
    # as instructed: "dead_code is gated on python_goldenmatch alone."
    violations = cfc.check_job_filter_coverage(ci_path=scratch_ci, filters_path=cfc.FILTERS)
    dead_code_violations = {p for j, p in violations if j == "dead_code"}

    assert dead_code_violations, (
        "sabotage did not reproduce: reverting the dead_code job's if: to "
        "python_goldenmatch alone must make the check report dead_code paths "
        "as uncovered (this is the #2839 incident itself)"
    )
    # A couple of the detector's own self-test files, specifically, so this
    # doesn't just pass because *something* got flagged.
    assert "scripts/test_dead_code_liveness.py" in dead_code_violations
    assert "scripts/test_no_new_dead_code.py" in dead_code_violations


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
