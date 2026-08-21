"""The refit's positive-evidence guard compares a SATURATING statistic.

`fs_refit_link_threshold` accepts a raised cutoff only when it reduces the
largest cluster. `_max_cluster_size` measures that with `build_clusters`, whose
signature is:

    build_clusters(pairs, all_ids=None, max_cluster_size=100, auto_split=True, ...)

`auto_split=True` MST-splits anything above `max_cluster_size`, so the function
can never return more than 100 no matter how badly the cutoff over-merges. Once
over-merge is severe enough to exceed the clamp at BOTH the default and the
candidate, the guard compares 100 against 100, `max_candidate >= max_default`
holds, and it declines.

The guard therefore refuses most confidently on the shape it exists to repair:
the worse the over-merge, the more certain that both cutoffs saturate and the
comparison carries no information.

Measured, person @ 1,000,000 rows (run 32075000216, `gm_probabilistic_shipped`),
recorded by the decision instrument rather than inferred:

    {"link_threshold": 0.5, "source": "fallback",
     "refit": {"reason": "no-max-reduction", "default_link": 0.5,
               "candidate": 0.6, "max_default": 100, "max_candidate": 100,
               "expelled_if_taken": 0.0, "expelled_cap": 0.01}}

Both sides pinned at exactly 100 -- the clamp value, not a coincidence -- while
the candidate would strand ZERO matched records. The fixture below reproduces
that record field-for-field.

## Why the fix is `auto_split=False` rather than a new criterion

Auto-split is a downstream MITIGATION: it chops an over-merged cluster into
presentable pieces. Measuring after it asks "how big are the pieces we cut this
into", which is capped by construction. The guard's question is whether the
CUTOFF reduced over-merge, and that is a property of the raw connected
components the cutoff produces.

This is deliberately not a new accept criterion. The alternative considered was
dropping the max requirement and letting `_expelled_share` carry the accept
alone -- the panel in `fs_refit_link_threshold`'s docstring shows `expelled`
classifying all three of its datasets correctly. It was rejected because that
same docstring names the blind spot it would open: "a candidate that splits a
correct cluster into two multi-member clusters expels nobody, so neither guard
sees it." The max test is the positive evidence guarding exactly that, so the
fix is to make it measure something true, not to delete it.

## Why the calibrated panel cannot move

Auto-split only fires above 100 members. Every cluster on the FS lever panel is
far below that (household_hardneg's over-merge is max 8 -> 3), so `auto_split`
never engages there and both statistics are unchanged. The behaviour change is
confined to datasets with components over 100 -- the population the panel does
not contain and where the guard is currently broken. Pinned in
`test_small_cluster_datasets_are_bit_identical`.
"""
from __future__ import annotations

from goldenmatch.core.cluster import build_clusters
from goldenmatch.core.probabilistic import (
    _expelled_share,
    _max_cluster_size,
    fs_refit_link_threshold,
)

_CLAMP = 100  # build_clusters' max_cluster_size default


def _oversized_overmerge(group: int = 150, groups: int = 8, weak: int = 60):
    """Eight true clusters of 150, bridged by a weak-scoring false-pair band.

    At 0.50 every group is chained into ONE component of 1200. At the 0.60
    valley the weak bridges drop and it resolves to the eight true groups of
    150 -- a genuine 8x over-merge repair that expels nobody, since every record
    stays in a 150-member cluster.

    Both 1200 and 150 exceed the clamp, which is the whole point: the reported
    max is 100 on both sides.
    """
    a: list[int] = []
    b: list[int] = []
    s: list[float] = []
    for g in range(groups):
        base = g * group
        for i in range(group - 1):
            a.append(base + i)
            b.append(base + i + 1)
            s.append(0.95)
    for g in range(groups - 1):
        left, right = g * group, (g + 1) * group
        for k in range(weak):
            a.append(left + k)
            b.append(right + k)
            s.append(0.55)
    return a, b, s


def _true_max(a, b, s, threshold: float) -> int:
    linked = [(int(x), int(y), float(z)) for x, y, z in zip(a, b, s) if z >= threshold]
    return max(c["size"] for c in build_clusters(linked, auto_split=False).values())


def test_the_fixture_really_is_an_overmerge_repair():
    """Guard the guard: if this stops being an 8x repair the test below proves
    nothing. Asserted on raw components, which is the ground truth here."""
    a, b, s = _oversized_overmerge()
    assert _true_max(a, b, s, 0.50) == 1200, "default should over-merge into one component"
    assert _true_max(a, b, s, 0.60) == 150, "the valley should resolve the true groups"
    assert _expelled_share(a, b, s, 0.50, 0.60) == 0.0, (
        "the repair regroups records, it must not strand any"
    )


def test_max_cluster_size_does_not_saturate_at_the_autosplit_clamp():
    """The defect itself. A 1200-record component must not report as 100."""
    a, b, s = _oversized_overmerge()
    assert _max_cluster_size(a, b, s, 0.50) == 1200, (
        "reported max is pinned at the auto-split clamp, so the guard's "
        "comparison carries no information about the cutoff"
    )


def test_severe_overmerge_is_repaired_rather_than_declined():
    """The user-visible consequence: an 8x repair that expels nobody is taken."""
    a, b, s = _oversized_overmerge()
    decision: dict = {}
    out = fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)

    assert decision["reason"] == "committed", (
        f"declined with {decision.get('reason')!r}: "
        f"max {decision.get('max_default')} -> {decision.get('max_candidate')}"
    )
    assert out == 0.60


def test_the_two_sides_are_not_both_the_clamp():
    """Pinned separately from the accept, because 'it committed' and 'it
    committed for a true reason' are different claims. A guard that accepted
    while still reading 100 -> 100 would be right by accident."""
    a, b, s = _oversized_overmerge()
    decision: dict = {}
    fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)
    assert not (decision["max_default"] == _CLAMP and decision["max_candidate"] == _CLAMP), (
        "both sides still report the clamp value"
    )
    assert decision["max_default"] > decision["max_candidate"] > 0


def test_small_cluster_datasets_are_bit_identical():
    """The calibrated panel must not move.

    Auto-split fires only above 100 members, so on panel-shaped data (max 8 -> 3)
    both statistics are unchanged. This pins the household_hardneg shape: a
    surname-collapsed cluster of 8 that the valley resolves to 3.
    """
    a: list[int] = []
    b: list[int] = []
    s: list[float] = []
    for c in range(120):                     # 120 clusters, well past _REFIT_MIN_PAIRS
        base = c * 8
        for i in range(7):                   # chained into 8
            a.append(base + i)
            b.append(base + i + 1)
            s.append(0.95 if i % 3 != 2 else 0.55)
    for t in (0.50, 0.60):
        assert _max_cluster_size(a, b, s, t) < _CLAMP, (
            "fixture must stay under the clamp or it tests the wrong thing"
        )
    assert _max_cluster_size(a, b, s, 0.50) == 8
    assert _max_cluster_size(a, b, s, 0.60) == 3


def test_shattering_is_still_rejected():
    """The blind spot the max test guards must stay guarded.

    Raising the cutoff here cuts true size-2 clusters, stranding both members --
    person's shape, where the refit costs -0.0616 F1. Making the max honest must
    not weaken this: it is `_expelled_share` that has to catch it.
    """
    a: list[int] = []
    b: list[int] = []
    s: list[float] = []
    for c in range(300):                     # 300 true PAIRS scoring below the valley
        a.append(2 * c)
        b.append(2 * c + 1)
        s.append(0.55)
    for c in range(300, 400):                # plus a strong mode so a valley exists
        a.append(2 * c)
        b.append(2 * c + 1)
        s.append(0.95)
    decision: dict = {}
    fs_refit_link_threshold(a, b, s, 0.50, decision_out=decision)
    assert decision["reason"] != "committed", (
        "cutting true pairs strands both members; this must never be accepted"
    )
