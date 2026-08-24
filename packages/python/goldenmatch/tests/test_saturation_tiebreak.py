"""A tiebreak that speaks only where the evidence is unambiguous.

`pick_committed`'s ranking terms are CONSTANT in 6/6 lanes (measured over 72
scored configs, docs/measurements/), so the lex order falls through to
`iteration` and v0 wins by construction. Once #2750 let the controller generate
a genuinely better candidate, that flat tiebreak began discarding it.

The obvious fix -- rank on `cluster_size_max`, the strongest measured signal --
carries the same trap that made an earlier `-sep` tiebreak REGRESS Abt-Buy
(0.0881 -> 0.0746): it is monotone against a non-monotone relationship. Below
the saturation bar the relationship inverts, and Amazon-Google's optimum
(cmax 18, F1 0.221) loses to a worse config (cmax 8, F1 0.110).

So the term is CLIPPED to the saturated region, where the measurements are
unambiguous on all three dedupe lanes:

    Abt-Buy   cmax 100 -> F1 .045,  90 -> .072,  73 -> .087,  56 -> .141
    Amz-Ggl   cmax 100 -> .038,     98 -> .041,  59 -> .110,  54 -> .115
    NCVR      cmax 100 -> .016,     99 -> .064,  95 -> .076

Below the bar it returns 0.0 for every candidate and changes nothing.
"""

from __future__ import annotations

from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
from goldenmatch.core.complexity_profile import (
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
    ZeroLabelConfidenceProfile,
)

_CAP = 100


def _entry(iteration: int, cmax: int, *, cap: int = _CAP,
           confidence: float | None = 0.2, sep: float = 0.554) -> HistoryEntry:
    zl = (None if confidence is None
          else ZeroLabelConfidenceProfile(overall_confidence=confidence))
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2173, n_cols=4),
        scoring=ScoringProfile(
            n_pairs_scored=500, candidates_compared=5000, candidates_counted=True,
            mass_above_threshold=0.9963, mass_in_borderline=0.9963 - sep,
        ),
        cluster=ClusterProfile(n_clusters=600, cluster_size_max=cmax,
                               transitivity_rate=1.0, max_cluster_size=cap),
        zero_label=zl,
    )
    return HistoryEntry(iteration=iteration, config=None, profile=profile,
                        decision=None, error=None, wall_clock_ms=1)


def _history(*entries: HistoryEntry) -> RunHistory:
    h = RunHistory()
    h.entries.extend(entries)
    return h


def test_a_less_saturated_candidate_beats_v0_when_confidence_is_flat():
    """The Abt-Buy shape after #2750: v0 pinned near the cap (cmax 90, F1 .088)
    against an iteration the controller steered to cmax 58 (F1 ~.135). Both RED,
    both at confidence 0.2 -- so without this term the tie goes to v0."""
    h = _history(_entry(3, 58), _entry(-1, 90))
    assert h.pick_committed(use_zero_label_confidence=True).iteration == 3


def test_below_the_bar_the_term_is_silent():
    """THE TRAP. Amazon-Google's optimum is cmax 18 (F1 0.221); cmax 8 scores
    0.110. A monotone `smaller is better` term would pick 8. Clipped, both read
    0.0, the tie falls through to `iteration`, and the conservative v0 default
    is preserved rather than replaced by a worse config."""
    h = _history(_entry(3, 8), _entry(-1, 18))
    assert h.pick_committed(use_zero_label_confidence=True).iteration == -1


def test_confidence_still_wins_when_it_discriminates():
    """The term sits UNDER `-overall_confidence` and cannot override it."""
    h = _history(_entry(0, 90, confidence=0.30), _entry(1, 50, confidence=0.20))
    assert h.pick_committed(use_zero_label_confidence=True).iteration == 0


def test_linkage_lanes_are_untouched():
    """`match_df` emits no cluster profile, so `max_cluster_size` is 0 and the
    term must not invent a ranking from a signal that was never measured."""
    h = _history(_entry(3, 0, cap=0), _entry(-1, 0, cap=0))
    assert h.pick_committed(use_zero_label_confidence=True).iteration == -1


def test_the_collapsed_regime_stays_neutral():
    """The precision-collapse branch deliberately falls through to `iteration`
    so v0 wins; the new slot must not reopen it (#195)."""
    h = _history(_entry(0, 50, sep=0.99), _entry(1, 55, sep=0.90), _entry(-1, 90, sep=0.50))
    picked = h.pick_committed(precision_collapse_floor=0.9,
                              use_zero_label_confidence=True)
    assert picked.iteration == -1


def test_ordering_within_the_saturated_region_matches_the_measurements():
    """Smaller saturation wins across the whole measured bad region."""
    for better, worse in ((56, 58), (58, 73), (73, 90), (90, 100)):
        h = _history(_entry(1, better), _entry(2, worse))
        assert h.pick_committed(use_zero_label_confidence=True).iteration == 1, (
            f"cmax {better} should beat {worse}"
        )
