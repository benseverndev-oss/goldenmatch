"""Lazy cluster-dict deferral (frames-out perf).

The frames-out pipeline path keeps clusters columnar (``ClusterFrames``) and
only needs the legacy ``dict[int, dict]`` shape when a consumer actually reads
``results["clusters"]`` / ``DedupeResult.clusters``. Building it eagerly cost
~3.6s on a 1M frames-out run (``cluster_frames_to_dict`` allocates ~900K
per-cluster dicts) -- pure waste for callers that never touch ``.clusters``
(the bench, stats-only consumers). ``LazyClusterDict`` defers the build to
first content access; ``results["cluster_stats"]`` carries the multi-member
count + matched-record count so ``_extract_stats`` no longer walks the dict.

This file locks:
  1. ``LazyClusterDict`` mechanics: it IS a dict, stays empty until a content
     method fires, builds exactly once, and is byte-identical once built
     (incl. copy/deepcopy/pickle).
  2. The frames-out pipeline leaves ``results["clusters"]`` a *lazy* dict, ships
     correct ``cluster_stats``, and the lazy dict materializes to the same
     content the eager path would build.
"""
from __future__ import annotations

import copy
import pickle

from goldenmatch.core.cluster import LazyClusterDict


def test_lazy_dict_defers_until_read():
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {1: {"members": [1, 2], "size": 2}, 5: {"members": [5], "size": 1}}

    d = LazyClusterDict(builder)
    assert isinstance(d, dict)
    assert calls["n"] == 0  # not built at construction

    assert len(d) == 2  # first content access builds
    assert calls["n"] == 1

    # every read path is served from the built contents, no rebuild
    assert d[1]["size"] == 2
    assert d.get(5)["size"] == 1
    assert d.get(99) is None
    assert 1 in d and 99 not in d
    assert list(d.keys()) == [1, 5]
    assert [k for k in d] == [1, 5]
    assert sum(v["size"] for v in d.values()) == 3
    assert bool(d) is True
    assert calls["n"] == 1


def test_lazy_dict_empty_builder_is_falsy_and_dict():
    d = LazyClusterDict(lambda: {})
    assert isinstance(d, dict)
    assert not d
    assert len(d) == 0
    assert dict(d) == {}


def test_lazy_dict_clear_before_build_stays_empty():
    """clear() on an unbuilt lazy dict must STICK: a later read must not rebuild
    and repopulate (real ``dict.clear()`` leaves it empty). It also must not
    force the build just to clear an empty store."""
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {1: {"members": [1, 2], "size": 2}}

    d = LazyClusterDict(builder)
    d.clear()
    assert calls["n"] == 0  # clear() does not force a build
    # a later content access sees the cleared (empty) state, NOT a rebuild
    assert len(d) == 0
    assert dict(d) == {}
    assert list(d.items()) == []
    assert calls["n"] == 0


def test_lazy_dict_clear_after_build_is_empty():
    d = LazyClusterDict(lambda: {1: {"members": [1, 2], "size": 2}})
    assert len(d) == 1  # builds
    d.clear()
    assert len(d) == 0
    assert dict(d) == {}


def test_lazy_dict_equals_plain_dict():
    payload = {1: {"members": [1, 2], "size": 2}, 5: {"members": [5], "size": 1}}
    d = LazyClusterDict(lambda: dict(payload))
    assert d == payload
    assert payload == d


def test_lazy_dict_copy_pickle_deepcopy_materialize_full():
    payload = {1: {"members": [1, 2], "size": 2}}

    shallow = copy.copy(LazyClusterDict(lambda: dict(payload)))
    assert type(shallow) is dict and shallow == payload

    deep = copy.deepcopy(LazyClusterDict(lambda: {1: {"members": [1, 2]}}))
    assert deep[1]["members"] == [1, 2]

    unp = pickle.loads(pickle.dumps(LazyClusterDict(lambda: dict(payload))))
    assert type(unp) is dict and unp[1]["size"] == 2


def test_dedupe_result_clusters_are_c_safe_for_lazy_handle():
    """DedupeResult.clusters must expose REAL contents to C-level consumers.

    Regression for the goldenmatch-pg ``p4_typed`` smoke ("expected 2 rows in a
    size-2 cluster, got 0"): the frames-out path stores a ``LazyClusterDict``
    whose underlying dict storage is empty until a Python content method fires
    ``_ensure()``. C-level consumers -- the pg bridge's pyo3
    ``.extract::<HashMap>()`` (``PyDict_Next``), ``json.dumps`` (empty-dict fast
    path via ``PyDict_GET_SIZE``) -- bypass those overrides and silently read
    ZERO clusters. ``DedupeResult.clusters`` is a property that materializes the
    lazy handle to a PLAIN dict on first read, closing that hole while keeping
    the "never built if never read" win.
    """
    import ctypes
    import json as _json

    from goldenmatch._api import DedupeResult

    payload = {1: {"members": [0, 1], "size": 2}, 2: {"members": [2], "size": 1}}
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return dict(payload)

    res = DedupeResult(clusters=LazyClusterDict(builder))
    # Construction must NOT build (the frames-out perf win).
    assert calls["n"] == 0
    assert type(res.__dict__.get("_clusters")) is LazyClusterDict

    clusters = res.clusters  # first read -> materialize to a plain dict
    assert type(clusters) is dict
    assert calls["n"] == 1

    # C-level views now see the real contents (were 0 / "{}" before the fix).
    assert ctypes.pythonapi.PyDict_Size(ctypes.py_object(clusters)) == 2
    assert _json.loads(_json.dumps(clusters)) == {
        "1": {"members": [0, 1], "size": 2},
        "2": {"members": [2], "size": 1},
    }
    # The pg bridge's own logic: one member-row per member, size-tagged.
    n_size2 = sum(
        len(info["members"]) for info in clusters.values() if len(info["members"]) == 2
    )
    assert n_size2 == 2

    # Subsequent reads are cached -- no rebuild.
    _ = len(res.clusters)
    assert calls["n"] == 1


def test_dedupe_result_clusters_default_and_plain_paths():
    """A DedupeResult with no clusters kwarg (or a plain dict) is a plain dict."""
    from goldenmatch._api import DedupeResult

    assert DedupeResult().clusters == {}
    assert type(DedupeResult().clusters) is dict
    plain = {7: {"members": [7], "size": 1}}
    assert DedupeResult(clusters=plain).clusters == plain


def _fixture_df():
    import polars as pl

    # A 2-member cluster (Bob/Bobby Brown, same zip), a 3-member weak chain,
    # and two singletons -- enough to exercise multi-member + matched counts.
    return pl.DataFrame(
        {
            "first_name": ["Bob", "Bobby", "Carl", "Carla", "Karl", "Dana", "Evan"],
            "last_name": ["Brown", "Brown", "Carter", "Carter", "Carter", "Dixon", "Ellis"],
            "zip": ["20002", "20002", "30003", "30003", "30003", "40004", "50005"],
        }
    )


def _config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        OutputConfig,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name_zip",
                fields=[
                    MatchkeyField(
                        column="last_name", transforms=["lowercase", "strip"],
                        scorer="jaro_winkler", weight=0.4,
                    ),
                    MatchkeyField(
                        column="first_name", transforms=["lowercase", "strip"],
                        scorer="jaro_winkler", weight=0.3,
                    ),
                    MatchkeyField(
                        column="zip", transforms=["strip"], scorer="exact", weight=0.3,
                    ),
                ],
                comparison="weighted",
                threshold=0.7,
            ),
        ],
        output=OutputConfig(format="csv", run_name="lazy_cluster_dict"),
    )


def test_pipeline_leaves_clusters_lazy_and_ships_stats(monkeypatch):
    from goldenmatch.core.pipeline import run_dedupe_df

    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    result = run_dedupe_df(_fixture_df(), _config(), source_name="t")

    # frames-out path -> clusters is the lazy handle, not yet materialized.
    clusters = result["clusters"]
    assert isinstance(clusters, LazyClusterDict)
    assert not clusters._built  # reading cluster_stats must not force it

    # cluster_stats is present and computed WITHOUT touching the dict.
    cs = result["cluster_stats"]
    assert not clusters._built
    assert cs["multi_member_cluster_count"] >= 1
    assert cs["matched_record_count"] >= 2

    # Materializing the dict yields content consistent with the shipped stats.
    materialized = dict(clusters.items())
    assert clusters._built
    multi = sum(1 for c in materialized.values() if c["size"] > 1)
    matched = sum(c["size"] for c in materialized.values() if c["size"] > 1)
    assert multi == cs["multi_member_cluster_count"]
    assert matched == cs["matched_record_count"]


def test_dedupe_df_stats_do_not_force_cluster_build(monkeypatch):
    import goldenmatch as gm
    import goldenmatch.core.cluster as cluster_mod
    import goldenmatch.core.pipeline as pipeline_mod

    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    runs = {"n": 0}
    orig = cluster_mod.cluster_frames_to_dict

    def counting(frames):
        runs["n"] += 1
        return orig(frames)

    monkeypatch.setattr(cluster_mod, "cluster_frames_to_dict", counting)
    monkeypatch.setattr(pipeline_mod, "cluster_frames_to_dict", counting)

    res = gm.dedupe_df(_fixture_df(), config=_config())

    # stats are populated but the cluster dict was never built.
    assert res.stats["total_clusters"] >= 1
    assert runs["n"] == 0

    # reading .clusters builds it exactly once, and it agrees with the stats.
    multi = sum(1 for c in res.clusters.values() if c["size"] > 1)
    assert runs["n"] == 1
    assert multi == res.stats["total_clusters"]
    matched = sum(c["size"] for c in res.clusters.values() if c["size"] > 1)
    assert matched == res.stats["matched_records"]
    _ = len(res.clusters)  # second access does not rebuild
    assert runs["n"] == 1
