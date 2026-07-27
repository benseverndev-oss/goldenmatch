"""Issue #1811 / #2006 (B2c): the FS columnar-cluster path threads the Arrow
pair table from ``score_buckets_arrow`` STRAIGHT into the frames-out clustering
(``build_cluster_frames`` + the native arrow kernel), Arrow end-to-end with NO
polars, so neither the driver-resident ``all_pairs`` Python ``list[tuple]`` nor
``build_clusters_columnar``'s per-cluster / 16M-key ``pair_scores`` dict is ever
built -- the late-stage OOM + the measured ~40s clustering cost at 5M of #1811.

Pins: (1) B2c is DEFAULT ON -- env unset routes an eligible single-FS-matchkey
bucket dedupe through ``build_cluster_frames`` with a ``pa.Table`` input, and
``build_clusters_columnar`` (the legacy dict path) is NOT called; (2) ``=0``
forces the legacy list path (``build_cluster_frames`` with a Python list);
(3) the columnar and list paths yield the same multi-member clusters on a
clear-margin fixture; (4) the review band survives the columnar path. The FS
bucket pipeline is ~0.1%-nondeterministic run-to-run at scale, so this is a
clear-margin equality fixture (the general gate is pair-set overlap); the full
peak-RSS + wall win rides the 64GB bench.
"""
from __future__ import annotations

import polars as pl
import pyarrow as pa
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


def _instrument(monkeypatch) -> dict:
    """Wrap both cluster builders; record call counts + the type of the first
    (``pairs``) arg passed to ``build_cluster_frames`` (list vs pa.Table)."""
    import goldenmatch.core.pipeline as P

    calls: dict = {"frames": 0, "columnar": 0, "frames_arg_types": []}
    _cf, _cc = P.build_cluster_frames, P.build_clusters_columnar

    def _wrap_cf(*a, **k):
        calls["frames"] += 1
        calls["frames_arg_types"].append(type(a[0]) if a else None)
        return _cf(*a, **k)

    def _wrap_cc(*a, **k):
        calls["columnar"] += 1
        return _cc(*a, **k)

    monkeypatch.setattr(P, "build_cluster_frames", _wrap_cf)
    monkeypatch.setattr(P, "build_clusters_columnar", _wrap_cc)
    return calls


def _run(monkeypatch, *, flag, cfg=None, df=None):
    import goldenmatch as gm

    if flag is None:
        monkeypatch.delenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", raising=False)
    else:
        monkeypatch.setenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", "1" if flag else "0")
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")
    return gm.dedupe_df(df if df is not None else _df(),
                        config=cfg if cfg is not None else _cfg(),
                        confidence_required=False)


def test_default_unset_routes_b2c_frames_arrow(monkeypatch):
    """#2006 DEFAULT ON: env unset -> B2c frames-out clustering with a pa.Table
    input; the legacy build_clusters_columnar dict path is NOT called."""
    calls = _instrument(monkeypatch)
    _run(monkeypatch, flag=None)
    assert calls["columnar"] == 0, "B2c must NOT use the legacy build_clusters_columnar dict path"
    assert any(t is pa.Table for t in calls["frames_arg_types"]), (
        "B2c must feed build_cluster_frames a pa.Table (Arrow end-to-end, no "
        f"polars); got {calls['frames_arg_types']}"
    )


def test_explicit_off_uses_list_path(monkeypatch):
    """`=0` forces the legacy list path: build_cluster_frames gets the all_pairs
    Python list, never a pa.Table; build_clusters_columnar stays unused."""
    calls = _instrument(monkeypatch)
    _run(monkeypatch, flag=False)
    assert calls["frames"] >= 1 and calls["columnar"] == 0
    assert all(t is not pa.Table for t in calls["frames_arg_types"]), (
        f"OFF path must pass the all_pairs list, not a pa.Table; got {calls['frames_arg_types']}"
    )


def test_columnar_matches_list_clusters(monkeypatch):
    """Clear-margin fixture: B2c (default) and the list path (=0) yield the same
    multi-member clusters."""
    on = _members(_run(monkeypatch, flag=None))
    off = _members(_run(monkeypatch, flag=False))
    assert off == {frozenset({0, 1, 2}), frozenset({3, 4})}, off  # fixture anchor
    assert on == off


def test_columnar_path_carries_review_band(monkeypatch):
    """The columnar (default) path must still attach the review band -- B2c
    previously left review_pairs empty, which would silently drop the review
    queue from every eligible FS dedupe once defaulted on. The review band
    (score in [review_cut, link_threshold)) rides the Arrow table and
    materializes to review_pairs; the linked set stays columnar."""
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
    res = _run(monkeypatch, flag=None, cfg=cfg, df=rows)
    assert calls["columnar"] == 0 and any(t is pa.Table for t in calls["frames_arg_types"]), \
        "must run the arrow columnar path"
    assert res.review_pairs, "columnar path must still attach the review band"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
