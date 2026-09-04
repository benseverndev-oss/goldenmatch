"""No NEW unenforced sync-claim may appear untriaged. Phase C, stage C3.

Same ratchet contract as `KNOWN_ACTIONABLE` in
scripts/test_no_new_shared_decisions.py: a floor to work DOWN, never a
bucket to top up. Both directions are asserted -- a new entry fails, and an
entry that no longer reproduces must be removed so the ratchet keeps its
value.

Gates `unenforced` specifically -- the tool's own most-trusted bucket
(high-confidence, non-ambiguous target, already coverage-rescue-informed).
Established as trustworthy by hand, not by construction: Stage 4b-4g
(docs/superpowers/specs/2026-09-04-stage4b-full-rescue-triage.md and
siblings) individually triaged the ENTIRE surrounding population -- every
`unenforced`, `unenforced_low_confidence`, `unenforced_ambiguous_target`,
and `unresolvable` claim, 218 in total -- and found real gaps concentrate
almost entirely in `unenforced`-shaped findings (Stage 4b: 8/25 mechanical
coverage-rescues were still real gaps) while `unenforced_low_confidence`
(3/111 = 2.7% real) and `unresolvable` (0/54 real) are too noisy to gate
without training everyone to ignore the gate. Those buckets stay
report-only, unchanged -- gating them is explicitly out of scope here, not
an oversight.

Requires coverage-informed inventory: coverage-rescue only ever REMOVES
claims from `unenforced` (a real test executing both halves is evidence
FOR enforcement, never against it), so a text-only run always over-reports
relative to what real CI computes with coverage data combined (measured
2026-09-04 on the same commit: 20 text-only vs. 5 coverage-informed). A run
where `.coverage` never got combined -- which only happens when
scripts/sync_claims/** changed without touching goldenmatch package/test
code, the one way the sync_claims job runs without python_goldenmatch/
_heavy also running, per .github/filters.yml -- has nothing new to catch by
construction (nothing in goldenmatch changed), so this SKIPS rather than
asserting against the noisier superset.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest
from sync_claims.report import DEFAULT_ROOT, DEFAULT_TESTS, inventory

REPO = Path(__file__).resolve().parent.parent
COVERAGE_DB = REPO / ".coverage"

# A floor to work DOWN. Each entry is (module, symbol, lineno) -- the same
# identity report.py's own `finding_ids` uses internally. Seeded 2026-09-04
# from a coverage-informed run against origin/main@9df5ba70f (gh run
# 33889747141's shard artifacts, combined the identical way the sync_claims
# CI job combines them) -- not a text-only run, which over-reports by
# exactly the claims coverage-rescue would remove.
KNOWN_ACTIONABLE: set[tuple[str, str, int]] = {
    ("backends/score_buckets.py", "_ensure_legal_forms_installed", 530),
    ("backends/score_buckets.py", "score_buckets", 1395),
    # False bare-word match: "row" resolved from prose describing "the
    # per-row path", not a declared symbol the claim actually names -- a
    # claims.py target-resolution limitation, not a real gap. Same shape
    # Stage 4f documented repeatedly in the low-confidence bucket
    # (LintInput->fields, _boundary_columns->keys, with_prose->pair, ...),
    # here landing at high confidence instead. Closing this means softening
    # the docstring so the pattern no longer resolves a target, not writing
    # a test against `row`.
    ("identity/resolve.py", "_batch_fingerprint_enabled", 203),
    ("identity/snowflake_backend.py", "_rel_expr", 501),
    ("spark/identity.py", "record_id_for_row", 45),
}


@lru_cache(maxsize=1)
def _inventory() -> dict:
    """Cached: both tests below need this, and a full run re-parses the
    whole package (982 test files, 305 claims measured 2026-09-04)."""
    coverage_db = COVERAGE_DB if COVERAGE_DB.exists() else None
    return inventory(DEFAULT_ROOT, DEFAULT_TESTS, coverage_db=coverage_db)


def _current() -> set[tuple[str, str, int]]:
    return {(c["module"], c["symbol"], c["lineno"]) for c in _inventory()["unenforced"]}


def _skip_if_degraded() -> None:
    if not _inventory()["counts"]["coverage_consulted"]:
        pytest.skip(
            "coverage db unavailable this run -- see module docstring: this "
            "only happens when nothing in goldenmatch changed, so there is "
            "nothing new for the gate to have missed."
        )


def test_no_new_unenforced_claim():
    """A high-confidence claim newly landing unenforced must be triaged.

    Triaged means one of: fix the real gap (write the test, or fix the
    code so the claim is true), soften the docstring so it stops making a
    claim `claims.py` resolves a target for at all, or add it to
    KNOWN_ACTIONABLE here with the reasoning -- the same three outcomes
    Stage 4b/4c/4f/4g used all day on the 218-claim population this floor
    was seeded from.
    """
    _skip_if_degraded()
    new = _current() - KNOWN_ACTIONABLE
    assert not new, (
        f"NEW unenforced high-confidence claim(s): {sorted(new)}. Each "
        f"names a docstring asserting an equivalence no single test "
        f"executes both halves of. Triage it (method: "
        f"docs/superpowers/specs/2026-09-04-stage4b-full-rescue-triage.md) "
        f"-- fix the real gap, soften the docstring if the claim isn't "
        f"real, or add it to KNOWN_ACTIONABLE here with the reason if it's "
        f"a known, not-yet-fixed finding."
    )


def test_findings_that_no_longer_reproduce_are_removed():
    """A floor to work DOWN. Keeping a fixed finding rots the ratchet."""
    _skip_if_degraded()
    fixed = KNOWN_ACTIONABLE - _current()
    assert not fixed, (
        f"{sorted(fixed)} no longer trip the unenforced signal -- fixed, "
        f"newly coverage-rescued, or the docstring changed. Remove them "
        f"from KNOWN_ACTIONABLE so the ratchet keeps its value."
    )
