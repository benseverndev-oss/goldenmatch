"""#2673/#2663/#2668: the honest admitted fraction, and the degenerate-empty guard.

`_emit_scoring_profile` is handed the pairs that ALREADY cleared the cut (its
own docstring: "pairs: Pairs *above* the threshold"), and computes
`mass_above(scores, threshold)` over exactly those. That is 1.0 by
construction for any non-empty result, at all six emit sites, for every
matchkey type -- so `mass_above_threshold` carries one bit ("did anything
match") while about a dozen consumers read it as a fraction.

`ScoringProfile.admitted_fraction` is the real thing: n_pairs_scored over
candidates_compared, or None when the scorer had no count to divide by.

It is a NEW field rather than a repair of the old one, and that was decided by
measurement, not taste. Rebasing `mass_above_threshold` itself was implemented
and reverted: the rules gate on hardcoded cuts (`< 0.5` in
rule_blocking_too_coarse, `>= 0.95` in the precision anchor, `>= 1.0` in
rule_recall_gap_suspected) chosen while that input was a CONSTANT, so making it
truthful invalidates every one of their calibrations at once. The quality gate
caught it: anchor_person_match F1 1.0000 -> 0.7303 (P 1.0000 -> 0.5751), the
controller taking a different rule path and over-merging.

So only two consumers read the new field, and only where it is not None:
`pick_committed`'s precision-collapse guard and `ScoringProfile.health()`'s
YELLOW branch (#2668, previously unreachable). Everything else stays on the
signal it was tuned against.

That alone did NOT fix #2663. The deciding consumer on `orgs_hard` turned out
to be zero-label confidence's everything-matches guard, and migrating THAT
also regressed the anchor (F1 1.0000 -> 0.5139) because its tautological cap
was the only thing penalising over-merge there.

What fixed it is the DEGENERATE-EMPTY guard in `pick_committed` -- the mirror
of the collapse guard: an entry that merged NOTHING must not beat one that
merged something. Chosen because, measured, no cluster-shape metric separates
`orgs_hard`'s correct entry from the anchor's over-merged one (giant 0.0154 vs
0.0156, oversized 0 both, bridge risk 0.0 both), while "did it merge anything"
separates them cleanly:

    orgs_hard    n_rows 845, v0 -> 845 clusters (0 merges)    BAD
    anchor       n_rows 706, v0 -> 400 clusters (306 merges)  GOOD

Result: orgs_hard F1 0.0000 -> 0.4108, anchor_person_match unchanged at 1.0000.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.complexity_profile import ScoringProfile
from goldenmatch.core.profile_emitter import profile_capture


def _emit(pairs, threshold, **kw):
    from goldenmatch.core.scorer import _emit_scoring_profile

    with profile_capture() as em:
        _emit_scoring_profile(pairs, threshold, **kw)
        return em.scoring


def test_mass_is_the_admitted_fraction_when_candidates_were_counted():
    """3 of 100 candidate pairs cleared the cut -> 0.03, not 1.0."""
    pairs = [(0, 1, 0.91), (0, 2, 0.95), (1, 2, 0.99)]
    sp = _emit(pairs, 0.9, candidates_compared=100, candidates_counted=True)
    assert sp.n_pairs_scored == 3
    assert sp.admitted_fraction == 0.03
    assert sp.mass_above_threshold == 1.0, "the old field keeps its old (tautological) meaning"


def test_a_genuinely_permissive_run_still_reads_high():
    """The signal must still be able to SAY 'everything matches' -- that is
    the pathology precision_collapse_floor exists to catch, and it becomes
    detectable again only because the denominator is now real."""
    pairs = [(i, i + 1, 0.99) for i in range(98)]
    sp = _emit(pairs, 0.5, candidates_compared=100, candidates_counted=True)
    assert sp.admitted_fraction == 0.98


def test_absent_candidate_count_reports_none_not_zero():
    """No denominator available -> no fraction can be invented, and ``None``
    is the only honest answer. ``0.0`` would read as "nothing matched", which
    is the absent-vs-zero collapse this whole change exists to remove
    (#2639/#2644)."""
    pairs = [(0, 1, 0.91)]
    sp = _emit(pairs, 0.9, candidates_compared=0, candidates_counted=False)
    assert sp.candidates_counted is False
    assert sp.admitted_fraction is None
    assert sp.mass_above_threshold == 1.0


def test_nothing_matched_is_zero_not_none():
    """A MEASURED zero is a real answer and must be distinguishable from the
    absent case above. Load-bearing for rule_no_matches and health()."""
    sp = _emit([], 0.9, candidates_compared=100, candidates_counted=True)
    assert sp.admitted_fraction == 0.0
    assert sp.mass_above_threshold == 0.0
    assert sp.n_pairs_scored == 0


def test_the_fraction_is_clamped_to_one():
    """Defensive: a candidate count smaller than the emitted pair count is a
    bug upstream, but it must not exceed 1.0 and silently trip every
    `>= 1.0` / `> 0.9` consumer -- the exact failure being fixed."""
    pairs = [(i, i + 1, 0.99) for i in range(10)]
    sp = _emit(pairs, 0.5, candidates_compared=4, candidates_counted=True)
    assert sp.admitted_fraction == 1.0


# ── the consumer this was breaking ─────────────────────────────────────────

def test_precision_collapse_guard_no_longer_demotes_a_selective_run():
    """`pick_committed` demoted every RED entry that matched anything, because
    mass_above was always 1.0 > the 0.9 floor. A selective run (3 of 100
    candidates) must now survive and beat a v0 that matched nothing."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ComplexityProfile,
        DataProfile,
    )

    def _entry(iteration: int, n_pairs: int, cand: int) -> HistoryEntry:
        return HistoryEntry(
            iteration=iteration,
            config=None,  # pick_committed only reads .profile / .error
            profile=ComplexityProfile(
                data=DataProfile(n_rows=845),
                blocking=BlockingProfile(n_blocks=180, reduction_ratio=0.989),
                scoring=ScoringProfile(
                    n_pairs_scored=n_pairs,
                    candidates_compared=cand,
                    candidates_counted=True,
                    admitted_fraction=(min(1.0, n_pairs / cand) if cand else 0.0),
                    mass_above_threshold=1.0 if n_pairs else 0.0,  # the old tautology, unchanged
                ),
            ),
            decision=None, error=None, wall_clock_ms=1,
        )

    history = RunHistory()
    # v0 matched nothing; iteration 2 matched 3 of 100 candidates.
    history.entries.append(_entry(2, 3, 100))
    history.entries.append(_entry(-1, 0, 100))
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    assert best.iteration == 2, (
        "the entry that actually matched something must win over a v0 that "
        "matched nothing; it is only 'precision collapse' if the fraction is "
        "genuinely near 1.0"
    )


# ── #2668: health() could never say YELLOW ─────────────────────────────────

def test_health_can_reach_yellow_again():
    """#2668 observed that `health()`'s only YELLOW branch --
    `mass_in_borderline > 0.3 and mass_in_borderline > mass_above_threshold`
    -- needed `mass_in_borderline > 1.0` once mass_above was pinned at 1.0.
    Impossible. So on any run that matched anything, health could only be
    GREEN or RED: there was no way to say "matched something, but it looks
    marginal", which is exactly the pressure that kept the controller from
    converging somewhere loose.
    """
    from goldenmatch.core.complexity_profile import HealthVerdict

    borderline_heavy = ScoringProfile(
        n_pairs_scored=40, candidates_compared=1000, candidates_counted=True,
        admitted_fraction=0.04, mass_above_threshold=1.0,
        mass_in_borderline=0.35, dip_statistic=0.02,
    )
    assert borderline_heavy.health() == HealthVerdict.YELLOW

    clean = ScoringProfile(
        n_pairs_scored=40, candidates_compared=1000, candidates_counted=True,
        admitted_fraction=0.04, mass_above_threshold=1.0,
        mass_in_borderline=0.01, dip_statistic=0.02,
    )
    assert clean.health() == HealthVerdict.GREEN


def test_the_collapse_guard_fires_on_a_genuine_everything_matches():
    """The other direction: the guard must still catch what it was written
    for. 990 of 1000 candidates admitted IS precision collapse, and now reads
    as 0.99 rather than being indistinguishable from a 3-in-1000 run."""
    permissive = ScoringProfile(
        n_pairs_scored=990, candidates_compared=1000, candidates_counted=True,
        admitted_fraction=0.99, mass_above_threshold=1.0,
        mass_in_borderline=0.0, dip_statistic=0.02,
    )
    assert permissive.admitted_fraction > 0.9, (
        "precision_collapse_floor=0.9 must still trip on a genuinely "
        "permissive config -- the fix restores the signal, it does not "
        "disable the guard"
    )


# ── end-to-end: #2663's own corpus ─────────────────────────────────

def test_orgs_hard_no_longer_returns_a_confident_empty_result():
    """#2663 end-to-end. `orgs_hard` is 845 rows with 1,055 true duplicate
    pairs, and zero-config returned 845 singleton clusters -- not a
    low-precision answer, an empty one, reported as a success.

    Fixed by the DEGENERATE-EMPTY guard in `pick_committed`, the mirror of
    the precision-collapse guard: an entry that merged NOTHING must not beat
    one that merged something. Measured, 2026-08-18:

        before   0 scored pairs, 845 clusters, F1 0.0000
        after  242 scored pairs, 607 clusters, P 0.5783 R 0.3185 F1 0.4108

    Not the ceiling -- the issue notes F1 0.6325 is reachable by forcing
    threshold=0.6, so threshold SELECTION remains a separate problem. What is
    fixed is the shape: a run that confidently found nothing.

    The floors below sit well under the measured values because the committed
    config is not bit-stable across environments; what must never regress is
    the shape.
    """
    import csv
    from pathlib import Path

    import goldenmatch
    import pyarrow as pa
    from goldenmatch.core.evaluate import evaluate_clusters

    base = (Path(__file__).resolve().parents[4]
            / "scripts/suggest_quality/corpora/orgs_hard")
    if not (base / "records.csv").exists():
        pytest.skip("orgs_hard corpus not present")

    with open(base / "records.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in rows[0] if c != "hardness"]
    df = pa.table({c: [r[c] for r in rows] for c in cols})

    with open(base / "truth.csv", newline="", encoding="utf-8") as f:
        truth = {
            (min(int(r["row_a"]), int(r["row_b"])),
             max(int(r["row_a"]), int(r["row_b"])))
            for r in csv.DictReader(f)
        }

    res = goldenmatch.dedupe_df(df)
    assert len(res.scored_pairs) > 0, (
        "zero-config found NOTHING on a corpus that is ~30% duplicates -- "
        "the #2663 regression, and the worst shape of wrong answer because "
        "it looks like a clean run"
    )
    assert len(res.clusters) < df.num_rows, "every record its own cluster"

    ev = evaluate_clusters(res.clusters, truth).summary()
    assert ev["recall"] >= 0.15, f"recall collapsed: {ev}"
    assert ev["precision"] >= 0.35, f"precision collapsed: {ev}"


def test_an_uncounted_route_keeps_its_prior_verdict():
    """The blast-radius guard, and the reason `admitted_fraction` is a NEW
    field instead of a repair of `mass_above_threshold`.

    Rebasing the old field was tried and reverted: about a dozen rules gate on
    hardcoded cuts (`< 0.5` in rule_blocking_too_coarse, `>= 0.95` in the
    precision anchor, `>= 1.0` in rule_recall_gap_suspected) that were all
    chosen while that input was a CONSTANT 1.0. Making it truthful invalidates
    their calibration at once -- measured on the quality gate,
    anchor_person_match went F1 1.0000 -> 0.7303 (P 1.0000 -> 0.5751) because
    the controller took a different rule path and over-merged.

    So a route that supplies no candidate count must behave EXACTLY as before:
    `admitted_fraction` is None, and both migrated consumers fall back to the
    old field rather than substituting a value.
    """
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ComplexityProfile,
        DataProfile,
        HealthVerdict,
    )

    uncounted = ScoringProfile(
        n_pairs_scored=40, candidates_compared=0, candidates_counted=False,
        admitted_fraction=None, mass_above_threshold=1.0,
        mass_in_borderline=0.35, dip_statistic=0.02,
    )
    # Old comparison (0.35 > 1.0) is False -> GREEN, exactly as before #2668.
    assert uncounted.health() == HealthVerdict.GREEN

    # And the collapse guard still demotes it on the old field, as before.
    def _entry(iteration: int, sp: ScoringProfile) -> HistoryEntry:
        return HistoryEntry(
            iteration=iteration, config=None,
            profile=ComplexityProfile(
                data=DataProfile(n_rows=845),
                blocking=BlockingProfile(n_blocks=1, reduction_ratio=0.01),
                scoring=sp,
            ),
            decision=None, error=None, wall_clock_ms=1,
        )

    red_uncounted = ScoringProfile(
        n_pairs_scored=40, candidates_compared=0, candidates_counted=False,
        admitted_fraction=None, mass_above_threshold=1.0,
        mass_in_borderline=0.0, dip_statistic=0.0,
    )
    history = RunHistory()
    history.entries.append(_entry(2, red_uncounted))
    history.entries.append(_entry(-1, red_uncounted))
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None and best.iteration == -1, (
        "with no candidate count the guard must keep reading the old field, "
        "so v0 still wins -- unchanged behaviour on an uncounted route"
    )


# ── the DEGENERATE-EMPTY guard (#2663) ─────────────────────────────────────

def _hist_entry(iteration: int, n_rows: int, n_clusters: int):
    """An entry whose only distinguishing feature is how much it merged."""
    from goldenmatch.core.autoconfig_history import HistoryEntry
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ClusterProfile,
        ComplexityProfile,
        DataProfile,
    )

    return HistoryEntry(
        iteration=iteration, config=None,
        profile=ComplexityProfile(
            data=DataProfile(n_rows=n_rows),
            blocking=BlockingProfile(n_blocks=180, reduction_ratio=0.989),
            scoring=ScoringProfile(
                n_pairs_scored=0 if n_clusters >= n_rows else 400,
                candidates_compared=9000, candidates_counted=True,
                admitted_fraction=0.0 if n_clusters >= n_rows else 0.05,
                mass_above_threshold=0.0 if n_clusters >= n_rows else 1.0,
            ),
            cluster=ClusterProfile(n_clusters=n_clusters, transitivity_rate=0.9),
        ),
        decision=None, error=None, wall_clock_ms=1,
    )


def test_an_entry_that_merged_nothing_loses_to_one_that_merged_something():
    """The #2663 mechanism. On `orgs_hard` v0 produced 845 clusters from 845
    rows -- zero merges -- and won every tiebreak, so the run reported no
    duplicates on data that is ~30% duplicates."""
    from goldenmatch.core.autoconfig_history import RunHistory

    history = RunHistory()
    history.entries.append(_hist_entry(3, n_rows=845, n_clusters=607))   # merged
    history.entries.append(_hist_entry(-1, n_rows=845, n_clusters=845))  # merged nothing
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None and best.iteration == 3, (
        "a config that found duplicates must beat one that found none"
    )


def test_when_every_entry_is_empty_the_ordering_is_unchanged():
    """Data that genuinely has no duplicates: every entry is demoted equally,
    so the guard is a no-op and v0 still wins as the safest fallback."""
    from goldenmatch.core.autoconfig_history import RunHistory

    history = RunHistory()
    history.entries.append(_hist_entry(3, n_rows=845, n_clusters=845))
    history.entries.append(_hist_entry(-1, n_rows=845, n_clusters=845))
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None and best.iteration == -1


def test_the_guard_never_outranks_health():
    """It only breaks ties WITHIN a health rank. A GREEN empty entry must
    still beat a RED one that merged -- the guard adds 1, and the RED/GREEN
    gap is also 1, so this pins that the tie resolves on iteration rather
    than letting 'merged something' override a worse verdict."""
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import HealthVerdict

    green_empty = _hist_entry(-1, n_rows=845, n_clusters=845)
    red_merged = _hist_entry(3, n_rows=845, n_clusters=607)
    assert green_empty.profile.health() == HealthVerdict.RED, (
        "fixture sanity: both are RED here, so this documents the rank "
        "arithmetic rather than asserting a GREEN/RED comparison"
    )
    history = RunHistory()
    history.entries.append(red_merged)
    history.entries.append(green_empty)
    assert history.pick_committed(precision_collapse_floor=0.9).iteration == 3
