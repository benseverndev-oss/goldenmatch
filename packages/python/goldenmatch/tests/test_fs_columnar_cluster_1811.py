"""Issue #1811 (B2c) — the opt-in FS columnar-cluster path
(`GOLDENMATCH_FS_COLUMNAR_CLUSTER`) threads the Arrow pair stream straight to
`build_clusters_columnar`, so the driver-resident `all_pairs` Python
`list[tuple]` is NEVER built during scoring -> clustering. At 14M on
tight-blocking/dup-dense data that list runs to hundreds of millions of tuples
held on the driver before clustering starts -- the late-stage OOM of #1811.

These pin: (1) the flag is default-OFF and inert (the list path runs); (2) with
the flag ON on an eligible single-FS-matchkey config, clustering consumes the
columnar DataFrame (build_clusters_columnar), NOT the list path
(build_cluster_frames); (3) the resulting clusters equal the list path on a
clear-margin fixture. Note: the FS bucket pipeline is ~0.1%-nondeterministic
run-to-run at scale, so this is a clear-margin equality fixture (the general
gate is pair-set overlap, not byte equality); the full peak-RSS win + 14M
confirmation ride the 64GB bench (the post-cluster scored_pairs materialization
is a tracked follow-up).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _df() -> pl.DataFrame:
    """Two entities of clear same-email near-dups + two singletons. Blocked by
    email so each entity is a bounded block; margins are wide (shared exact
    email + near-identical first name) so the result is deterministic."""
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "first_name": ["john", "john", "jon", "mary", "mary", "zoe"],
        "last_name": ["smith", "smith", "smith", "jones", "jones", "xu"],
        "email": ["j@x.com", "j@x.com", "j@x.com", "m@x.com", "m@x.com", "z@x.com"],
    })


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
        backend="bucket",
    )


def _members(res) -> frozenset:
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in (res.clusters or {}).values()
        if len(c.get("members", [])) > 1
    )


def _run(monkeypatch, *, flag: bool):
    import goldenmatch as gm

    monkeypatch.setenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", "1" if flag else "0")
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")
    return gm.dedupe_df(_df(), config=_cfg(), confidence_required=False)


def test_flag_off_uses_list_path(monkeypatch):
    """Default-OFF: the list path (build_cluster_frames) runs, unchanged."""
    import goldenmatch.core.pipeline as P

    calls = {"frames": 0, "columnar": 0}
    _cf, _cc = P.build_cluster_frames, P.build_clusters_columnar
    monkeypatch.setattr(P, "build_cluster_frames",
                        lambda *a, **k: (calls.__setitem__("frames", calls["frames"] + 1), _cf(*a, **k))[1])
    monkeypatch.setattr(P, "build_clusters_columnar",
                        lambda *a, **k: (calls.__setitem__("columnar", calls["columnar"] + 1), _cc(*a, **k))[1])
    _run(monkeypatch, flag=False)
    assert calls["frames"] >= 1 and calls["columnar"] == 0


def test_flag_on_uses_columnar_path(monkeypatch):
    """ON: clustering consumes the columnar DataFrame; the all_pairs list path
    (build_cluster_frames) is NOT taken."""
    import goldenmatch.core.pipeline as P

    calls = {"frames": 0, "columnar": 0}
    _cf, _cc = P.build_cluster_frames, P.build_clusters_columnar
    monkeypatch.setattr(P, "build_cluster_frames",
                        lambda *a, **k: (calls.__setitem__("frames", calls["frames"] + 1), _cf(*a, **k))[1])
    monkeypatch.setattr(P, "build_clusters_columnar",
                        lambda *a, **k: (calls.__setitem__("columnar", calls["columnar"] + 1), _cc(*a, **k))[1])
    _run(monkeypatch, flag=True)
    assert calls["columnar"] >= 1, "flag ON must route clustering through the columnar path"
    assert calls["frames"] == 0, "flag ON must NOT build the all_pairs list for clustering"


def test_flag_on_matches_off_clusters(monkeypatch):
    """Clear-margin fixture: the columnar path yields the same multi-member
    clusters as the list path."""
    off = _members(_run(monkeypatch, flag=False))
    on = _members(_run(monkeypatch, flag=True))
    assert off == {frozenset({0, 1, 2}), frozenset({3, 4})}, off  # fixture anchor
    assert on == off


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
