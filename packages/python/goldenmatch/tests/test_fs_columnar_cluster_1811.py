"""Issue #1811 (B2c) — the opt-in FS columnar-cluster path
(`GOLDENMATCH_FS_COLUMNAR_CLUSTER`) threads the Arrow pair stream straight to
`build_clusters_columnar`, so the driver-resident `all_pairs` Python
`list[tuple]` is NEVER built during scoring -> clustering. At 14M on
tight-blocking/dup-dense data that list runs to hundreds of millions of tuples
held on the driver before clustering starts -- the late-stage OOM of #1811.

These pin: (1) B2c is now DEFAULT ON (#2006) -- with the env var UNSET an
eligible single-FS-matchkey bucket config routes clustering through the
columnar DataFrame (build_clusters_columnar); (2) the escape hatch
``GOLDENMATCH_FS_COLUMNAR_CLUSTER=0`` forces the legacy all_pairs list path
(build_cluster_frames); (3) the columnar and list paths yield the same
multi-member clusters on a clear-margin fixture. Note: the FS bucket pipeline
is ~0.1%-nondeterministic run-to-run at scale, so this is a clear-margin
equality fixture (the general gate is pair-set overlap, not byte equality); the
full peak-RSS win + 14M confirmation ride the 64GB bench.
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


def _instrument(monkeypatch):
    """Wrap both cluster builders with call counters; return the counter dict."""
    import goldenmatch.core.pipeline as P

    calls = {"frames": 0, "columnar": 0}
    _cf, _cc = P.build_cluster_frames, P.build_clusters_columnar
    monkeypatch.setattr(P, "build_cluster_frames",
                        lambda *a, **k: (calls.__setitem__("frames", calls["frames"] + 1), _cf(*a, **k))[1])
    monkeypatch.setattr(P, "build_clusters_columnar",
                        lambda *a, **k: (calls.__setitem__("columnar", calls["columnar"] + 1), _cc(*a, **k))[1])
    return calls


def test_default_unset_uses_columnar_path(monkeypatch):
    """#2006 DEFAULT ON: with the env var UNSET, an eligible single-FS-matchkey
    bucket dedupe routes clustering through the columnar path -- the all_pairs
    list path (build_cluster_frames) is NOT built."""
    import goldenmatch as gm

    monkeypatch.delenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", raising=False)
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")
    calls = _instrument(monkeypatch)
    gm.dedupe_df(_df(), config=_cfg(), confidence_required=False)
    assert calls["columnar"] >= 1, "default (env unset) must route through the columnar path"
    assert calls["frames"] == 0, "default must NOT build the all_pairs list for clustering"


def test_explicit_off_uses_list_path(monkeypatch):
    """Escape hatch: GOLDENMATCH_FS_COLUMNAR_CLUSTER=0 forces the legacy list
    path (build_cluster_frames)."""
    calls = _instrument(monkeypatch)
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


def test_columnar_path_carries_review_band(monkeypatch):
    """#2006 regression guard: the columnar path (default ON) must still attach
    the review band -- B2c previously dropped review_pairs, which would silently
    remove the review queue from every eligible FS dedupe once defaulted on. The
    review band (score in [review_cut, link_threshold)) rides the Arrow table and
    is materialized to review_pairs, while the linked set stays columnar."""
    import goldenmatch as gm

    monkeypatch.delenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", raising=False)
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")
    # borderline near-dups need the legacy emit-at-neutral behavior to reach the
    # review band (mirrors TestProbabilisticReviewCandidates).
    monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "0")

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs_review", type="probabilistic", fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
        ], link_threshold=0.9, review_threshold=0.4)],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend="bucket",
    )
    rows = pl.DataFrame({"name": ["John", "Jon", "John"], "zip": ["x", "x", "x"]})

    calls = _instrument(monkeypatch)
    res = gm.dedupe_df(rows, config=cfg, confidence_required=False)
    assert calls["columnar"] >= 1 and calls["frames"] == 0, "must run the columnar path"
    assert res.review_pairs, "columnar path must still attach the review band"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
