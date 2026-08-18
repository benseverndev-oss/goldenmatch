"""#2673: mass_above_threshold must be a fraction of CANDIDATES, not of matches.

`_emit_scoring_profile` is handed the pairs that ALREADY cleared the cut (its
own docstring: "pairs: Pairs *above* the threshold"), and computed
`mass_above(scores, threshold)` over exactly those. That is 1.0 by
construction for any non-empty result, at all six emit sites, for every
matchkey type -- so the field carried one bit ("did anything match") while a
dozen consumers read it as a fraction.

The damage is at commit time: `pick_committed` demotes any RED entry whose
`mass_above > 0.9` as the "everything matches" pathology, which -- given the
tautology -- is every RED entry that matched anything at all. v0, which
matched nothing, wins. That is the confident-empty-result in #2663.

`candidates_compared` is the denominator this always wanted, and
`_emit_scoring_profile` already takes it as a parameter. When it is real
(`candidates_counted=True`) the honest fraction is
`n_pairs_scored / candidates_compared`. When it is absent, the old bit is
still the best available answer and is kept -- but consumers that need a
fraction must branch on `candidates_counted` rather than trusting it.
"""
from __future__ import annotations

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
    assert sp.mass_above_threshold == 0.03


def test_a_genuinely_permissive_run_still_reads_high():
    """The signal must still be able to SAY 'everything matches' -- that is
    the pathology precision_collapse_floor exists to catch, and it becomes
    detectable again only because the denominator is now real."""
    pairs = [(i, i + 1, 0.99) for i in range(98)]
    sp = _emit(pairs, 0.5, candidates_compared=100, candidates_counted=True)
    assert sp.mass_above_threshold == 0.98


def test_absent_candidate_count_keeps_the_old_bit():
    """No denominator available -> no fraction can be invented. The old
    behaviour (1.0 when something matched) is retained as the honest bit,
    and `candidates_counted=False` is what tells consumers not to read it
    as a fraction (the #2639/#2644 idiom)."""
    pairs = [(0, 1, 0.91)]
    sp = _emit(pairs, 0.9, candidates_compared=0, candidates_counted=False)
    assert sp.candidates_counted is False
    assert sp.mass_above_threshold == 1.0


def test_nothing_matched_is_still_zero():
    """The zero end is load-bearing for rule_no_matches and health()."""
    sp = _emit([], 0.9, candidates_compared=100, candidates_counted=True)
    assert sp.mass_above_threshold == 0.0
    assert sp.n_pairs_scored == 0


def test_the_fraction_is_clamped_to_one():
    """Defensive: a candidate count smaller than the emitted pair count is a
    bug upstream, but it must not produce mass > 1.0 and silently trip every
    `>= 1.0` consumer."""
    pairs = [(i, i + 1, 0.99) for i in range(10)]
    sp = _emit(pairs, 0.5, candidates_compared=4, candidates_counted=True)
    assert sp.mass_above_threshold == 1.0


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
                    mass_above_threshold=(min(1.0, n_pairs / cand) if cand else 0.0),
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
        mass_above_threshold=0.04, mass_in_borderline=0.35, dip_statistic=0.02,
    )
    assert borderline_heavy.health() == HealthVerdict.YELLOW

    clean = ScoringProfile(
        n_pairs_scored=40, candidates_compared=1000, candidates_counted=True,
        mass_above_threshold=0.04, mass_in_borderline=0.01, dip_statistic=0.02,
    )
    assert clean.health() == HealthVerdict.GREEN


def test_the_collapse_guard_fires_on_a_genuine_everything_matches():
    """The other direction: the guard must still catch what it was written
    for. 990 of 1000 candidates admitted IS precision collapse, and now reads
    as 0.99 rather than being indistinguishable from a 3-in-1000 run."""
    permissive = ScoringProfile(
        n_pairs_scored=990, candidates_compared=1000, candidates_counted=True,
        mass_above_threshold=0.99, mass_in_borderline=0.0, dip_statistic=0.02,
    )
    assert permissive.mass_above_threshold > 0.9, (
        "precision_collapse_floor=0.9 must still trip on a genuinely "
        "permissive config -- the fix restores the signal, it does not "
        "disable the guard"
    )


# ── end-to-end: #2663's own corpus ─────────────────────────────────────────

def test_orgs_hard_no_longer_returns_a_confident_empty_result():
    """#2663 end-to-end. `orgs_hard` is 845 rows with 1,055 true duplicate
    pairs, and zero-config returned 845 singleton clusters -- not a
    low-precision answer, an empty one, reported as a success.

    Measured on this fix (2026-08-18): 242 scored pairs, 607 clusters,
    P 0.5783 / R 0.3185 / F1 0.4108. The issue notes F1 0.6325 is reachable
    on this corpus by forcing threshold=0.6, so this is not the ceiling --
    threshold SELECTION is a separate problem. The floors below are set well
    under the measured values because the controller's committed config is
    not bit-stable across environments; what must never regress is the shape
    -- a run that finds nothing at all.
    """
    import csv
    from pathlib import Path

    import goldenmatch
    import pyarrow as pa
    import pytest
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
