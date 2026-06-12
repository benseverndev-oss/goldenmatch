"""Routing tests for _phase5_cluster (#844 Spec 2). No Ray runtime: the
clustering functions are monkeypatched, so the branch is tested in isolation."""


def test_phase5_cluster_uses_wcc_when_block_shuffle_on(monkeypatch):
    import goldenmatch.distributed.clustering as C
    import goldenmatch.distributed.pipeline as P
    import goldenmatch.distributed.scoring as S

    calls = {}
    monkeypatch.setattr(C, "build_clusters_distributed",
                        lambda ds, **kw: (calls.__setitem__("wcc", kw), "WCC")[1])
    monkeypatch.setattr(C, "local_cc_assignments",
                        lambda ds: (calls.__setitem__("local", True), "LOCAL")[1])
    monkeypatch.setattr(S, "_block_shuffle_enabled", lambda: True)
    monkeypatch.setattr(S, "_has_colocation_plan", lambda cfg: True)

    out = P._phase5_cluster("PAIRS_DS", object())
    assert out == "WCC"
    assert calls["wcc"]["algorithm"] == "randomized_contraction"
    assert calls["wcc"]["all_ids"] is None
    assert "local" not in calls


def test_phase5_cluster_uses_local_cc_when_block_shuffle_off(monkeypatch):
    import goldenmatch.distributed.clustering as C
    import goldenmatch.distributed.pipeline as P
    import goldenmatch.distributed.scoring as S

    calls = {}
    monkeypatch.setattr(C, "build_clusters_distributed",
                        lambda ds, **kw: (calls.__setitem__("wcc", kw), "WCC")[1])
    monkeypatch.setattr(C, "local_cc_assignments",
                        lambda ds: (calls.__setitem__("local", True), "LOCAL")[1])
    monkeypatch.setattr(S, "_block_shuffle_enabled", lambda: False)
    monkeypatch.setattr(S, "_has_colocation_plan", lambda cfg: True)

    out = P._phase5_cluster("PAIRS_DS", object())
    assert out == "LOCAL"
    assert "wcc" not in calls


def test_phase5_cluster_local_cc_when_no_colocation_plan(monkeypatch):
    """Block-shuffle flag set but the config has no co-location plan -> the
    shuffle scorer didn't fire, so clustering stays per-partition local_cc."""
    import goldenmatch.distributed.clustering as C
    import goldenmatch.distributed.pipeline as P
    import goldenmatch.distributed.scoring as S

    calls = {}
    monkeypatch.setattr(C, "build_clusters_distributed",
                        lambda ds, **kw: (calls.__setitem__("wcc", kw), "WCC")[1])
    monkeypatch.setattr(C, "local_cc_assignments",
                        lambda ds: (calls.__setitem__("local", True), "LOCAL")[1])
    monkeypatch.setattr(S, "_block_shuffle_enabled", lambda: True)
    monkeypatch.setattr(S, "_has_colocation_plan", lambda cfg: False)

    out = P._phase5_cluster("PAIRS_DS", object())
    assert out == "LOCAL"
    assert "wcc" not in calls
