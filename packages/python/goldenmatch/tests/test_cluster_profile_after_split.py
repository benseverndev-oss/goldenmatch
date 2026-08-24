"""The profile must describe the clusters the run RETURNS, not the pre-split ones.

`_emit_cluster_profile_frames` runs inside clustering; the transitive-consistency
split runs later, during results assembly. Instrumenting a real run shows the
order plainly:

    call order: [('emit_frames', ...), ('split', 36, 36)]

So the controller's `ClusterProfile` always described PRE-split clusters.
Measured on Abt-Buy: enabling splitting moved the final cluster count 669 -> 709
while the profile reported 669 both times, and the only field that differed was
`transitivity_rate` by 0.004 -- inside the triple sampler's noise band. That is
why `pick_committed` could never prefer the iteration that enabled splitting; it
looked identical to the one that did not.

These tests assert the ORDER and the re-emitted VALUE rather than driving a
frame whose split is non-trivial. Three synthetic fixtures were tried first --
chains, two-cliques-plus-bridge -- and `token_sort` merged every one into a
single cluster the splitter then declined to cut, so the assertion would have
passed while measuring nothing. The real non-trivial case is Abt-Buy, covered by
`scripts/diagnose_cluster_profile_visibility.py`.
"""

from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import ClusterConfig, GoldenMatchConfig
from goldenmatch.core.profile_emitter import profile_capture


def _frame(n_groups: int = 6) -> pl.DataFrame:
    rows = []
    for g in range(n_groups):
        rows += [
            {"name": f"alpha{g} alpha{g} one", "city": "sf"},
            {"name": f"alpha{g} omega{g}", "city": "sf"},
            {"name": f"omega{g} omega{g} two", "city": "sf"},
        ]
    return pl.DataFrame(rows)


def _run_with_spies(monkeypatch, *, splitting: bool):
    """Run a dedupe, recording the order of profile emits and the split."""
    import goldenmatch
    import goldenmatch.core.cluster as cluster_mod
    import goldenmatch.core.transitive_consistency as tc_mod

    order: list[tuple] = []
    real_dict = cluster_mod._emit_cluster_profile
    real_frames = cluster_mod._emit_cluster_profile_frames
    real_split = tc_mod.materialize_and_split

    # Signatures mirror the emitters', including `max_cluster_size` (#2750) --
    # the call sites pass it positionally, so a spy that omits it fails with a
    # TypeError that looks like an ordering bug rather than a stale stub.
    def dict_spy(clusters, max_cluster_size=0):
        order.append(("emit", len(clusters)))
        return real_dict(clusters, max_cluster_size)

    def frames_spy(metadata, assignments, pairs=None, max_cluster_size=0):
        order.append(("emit", None))
        return real_frames(metadata, assignments, pairs, max_cluster_size)

    def split_spy(clusters, all_pairs, margin=None):
        out, report = real_split(clusters, all_pairs, margin)
        order.append(("split", len(clusters), len(out)))
        return out, report

    monkeypatch.setattr(cluster_mod, "_emit_cluster_profile", dict_spy)
    monkeypatch.setattr(cluster_mod, "_emit_cluster_profile_frames", frames_spy)
    monkeypatch.setattr(tc_mod, "materialize_and_split", split_spy)

    cfg = None
    if splitting:
        cfg = GoldenMatchConfig(
            cluster=ClusterConfig(split_weak_bridges=True, weak_bridge_margin=0.0)
        )
    with profile_capture() as emitter:
        kwargs = {"fuzzy": {"name": 1.0}, "threshold": 0.5, "blocking": ["city"]}
        if cfg is not None:
            kwargs["config"] = cfg
        result = goldenmatch.dedupe_df(_frame(), **kwargs)
        profile = emitter.cluster
    return order, profile, result


def test_an_emit_follows_the_split(monkeypatch):
    """The fix, stated as an ordering invariant: whatever else happens, the LAST
    profile emit must come after the split, or the profile describes clusters the
    run does not return."""
    order, _profile, _result = _run_with_spies(monkeypatch, splitting=True)
    kinds = [step[0] for step in order]
    assert "split" in kinds, "splitting did not run -- the fixture is not exercising it"
    assert kinds[-1] == "emit", (
        f"the split is the last thing to touch the clusters: {order}. The profile "
        f"therefore describes pre-split clusters."
    )


def test_the_reemitted_profile_matches_what_the_run_returns(monkeypatch):
    order, profile, result = _run_with_spies(monkeypatch, splitting=True)
    assert profile is not None
    assert profile.n_clusters == len(result.clusters), (
        f"profile says {profile.n_clusters}, run returned {len(result.clusters)}; "
        f"order was {order}"
    )


def test_nothing_extra_runs_when_splitting_is_off(monkeypatch):
    """Default off must stay byte-identical: no split, no re-emit, no added cost."""
    order, profile, result = _run_with_spies(monkeypatch, splitting=False)
    assert [step[0] for step in order].count("split") == 0
    assert [step[0] for step in order].count("emit") == 1
    assert profile is not None
    assert profile.n_clusters == len(result.clusters)
