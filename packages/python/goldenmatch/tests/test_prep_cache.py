"""Unit tests for the prep-step cache in core/pipeline.py.

Attack C of the map_elements perf spec
(docs/superpowers/specs/2026-05-15-map-elements-attack-design.md).

The cache memoizes GoldenCheck quality scan + GoldenFlow transform +
auto-fix output for the duration of a process. The auto-config controller
calls ``dedupe_df(sample, config)`` ~5x with the same sample object per
``auto_configure_df`` invocation; caching here means iterations 2-5 skip
the deterministic prep work entirely.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
from goldenmatch.core.pipeline import (
    _PREP_CACHE,
    _PREP_CACHE_LRU,
    _PREP_CACHE_MAX,
    _prep_cache_clear,
    _prep_cache_signature,
)


def _make_df() -> pl.DataFrame:
    return pl.DataFrame({
        "name":  ["Alice  ", "Alice", "Bob ", "Bobby"],
        "email": ["A@x.com", "a@x.com", "B@y.com", "B@y.com"],
    })


def test_prep_cache_starts_empty():
    _prep_cache_clear()
    assert _PREP_CACHE == {}
    assert _PREP_CACHE_LRU == []


def test_dedupe_populates_cache():
    """First gm.dedupe_df() call should leave one entry in the cache."""
    _prep_cache_clear()
    df = _make_df()
    gm.dedupe_df(df, fuzzy={"name": 0.7})
    # Controller iterates the pipeline several times; the eventual final
    # full-data call also hits the pipeline. Either way, the cache should
    # have at least one entry by now.
    assert len(_PREP_CACHE) >= 1


def test_controller_iterations_hit_cache():
    """The whole point of Attack C: the controller calls dedupe_df ~5x
    on the same sample per auto_configure_df call. With the cache working,
    run_transform should fire **once**, not five times.

    Without the cache seeded by the caller's df id, each iteration's
    fresh LazyFrame wrapping would have a different id() and the cache
    would never hit. This is the load-bearing regression test that
    surfaced the original cache-seed bug.
    """
    _prep_cache_clear()
    import goldenmatch.core.transform as tm
    original = tm.run_transform
    call_count = [0]

    def counting(*args, **kwargs):
        call_count[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        df = _make_df()
        gm.dedupe_df(df, fuzzy={"name": 0.7})
    finally:
        tm.run_transform = original
    # One call per dedupe_df invocation: the first iteration populates
    # the cache; iterations 2-5 hit and skip the entire prep block.
    assert call_count[0] == 1, (
        f"run_transform called {call_count[0]} times "
        f"(expected 1 — cache should be hitting on iterations 2-5)"
    )


def test_repeated_dedupe_same_df_uses_cache():
    """Calling dedupe_df twice with the same df object should hit the cache.

    Hits aren't directly observable (cache is internal), but cache size
    must not grow beyond what the controller normally produces. Critical
    invariant: results are identical across calls.
    """
    _prep_cache_clear()
    df = _make_df()
    result_a = gm.dedupe_df(df, fuzzy={"name": 0.7})
    result_b = gm.dedupe_df(df, fuzzy={"name": 0.7})
    # Same input → same cluster output.
    a_clusters = {tuple(sorted(c["members"])) for c in result_a.clusters.values()}
    b_clusters = {tuple(sorted(c["members"])) for c in result_b.clusters.values()}
    assert a_clusters == b_clusters
    # Cache must not have grown unboundedly. Because controller iterates
    # internally on the SAMPLE (different id than `df`), the second call
    # creates its own sample with a fresh id. So cache may grow by a few
    # entries on the second call but stays bounded by _PREP_CACHE_MAX.
    assert len(_PREP_CACHE) <= _PREP_CACHE_MAX


def test_lru_eviction():
    """When more than _PREP_CACHE_MAX distinct (id, sig) pairs land in
    the cache, the oldest entries get evicted FIFO."""
    _prep_cache_clear()
    # Dedupe N distinct dataframes (distinct id() each) and confirm the
    # cache stays bounded.
    for i in range(_PREP_CACHE_MAX + 3):
        df = pl.DataFrame({
            "name": [f"row_{i}_a", f"row_{i}_a", f"row_{i}_b"],
            "email": [f"a{i}@x.com", f"a{i}@x.com", f"b{i}@x.com"],
        })
        gm.dedupe_df(df, fuzzy={"name": 0.7})
    assert len(_PREP_CACHE) <= _PREP_CACHE_MAX, (
        f"cache exceeded max: {len(_PREP_CACHE)} > {_PREP_CACHE_MAX}"
    )


def test_cache_seed_encodes_row_height():
    """The prep-cache seed is ``(id(df), df.height)``, not bare ``id(df)``.

    CPython recycles ``id()`` slots after GC, so a stale cache entry whose
    key was built from a now-dead frame can collide with a fresh frame that
    lands on the same address. The schema-name fingerprint in the key does
    NOT discriminate same-schema inputs, so an empty df and a populated df of
    the same schema could collide — the ``test_dedupe_df_empty`` flake
    (`assert 3 == 0`). Folding height into the seed distinguishes them.
    """
    _prep_cache_clear()
    df = pl.DataFrame({
        "name": ["Alice", "Alice", "Bob"],
        "email": ["a@x.com", "a@x.com", "b@y.com"],
    })
    gm.dedupe_df(df, exact=["email"])
    seeds = {k[0] for k in _PREP_CACHE}
    assert (id(df), df.height) in seeds, (
        f"expected seed (id(df), height={df.height}) in cache seeds {seeds}"
    )


def test_empty_df_does_not_collide_with_populated_prep():
    """Empty-input dedupe returns 0 rows and its cache seed carries height 0,
    so a recycled id() slot can never serve it a populated frame's prep.

    Regression for the ``test_dedupe_df_empty`` xdist flake: an empty df and
    a 3-row df of identical schema/config differ only in height, which the
    seed now captures.
    """
    _prep_cache_clear()
    empty = pl.DataFrame({"email": []}).cast({"email": pl.Utf8})
    result = gm.dedupe_df(empty, exact=["email"])
    assert result.total_records == 0
    seeds = {k[0] for k in _PREP_CACHE}
    assert (id(empty), 0) in seeds, (
        f"expected empty-df seed (id, height=0) in cache seeds {seeds}"
    )


def test_prep_cache_signature_quality_change_misses():
    """Different quality.mode → different signature → cache miss."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        QualityConfig,
    )
    mk = MatchkeyConfig(
        name="m", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    block = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["name"])], max_block_size=1000,
    )
    cfg_enabled = GoldenMatchConfig(
        matchkeys=[mk], blocking=block, quality=QualityConfig(mode="enabled"),
    )
    cfg_disabled = GoldenMatchConfig(
        matchkeys=[mk], blocking=block, quality=QualityConfig(mode="disabled"),
    )
    assert _prep_cache_signature(cfg_enabled) != _prep_cache_signature(cfg_disabled)


def test_prep_cache_signature_matchkey_change_does_not_miss():
    """Matchkey changes do NOT change the prep-cache signature — the prep
    steps don't depend on matchkey config. This is the load-bearing
    invariant that makes the controller-iteration cache hits possible:
    iterating matchkey/blocking/threshold doesn't bust the cache."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    mk_v1 = MatchkeyConfig(
        name="m", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    mk_v2 = MatchkeyConfig(
        name="m", type="weighted", threshold=0.7,   # different threshold
        fields=[MatchkeyField(field="name", scorer="token_sort", weight=2.0)],  # different scorer + weight
    )
    block = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["name"])], max_block_size=1000,
    )
    cfg_v1 = GoldenMatchConfig(matchkeys=[mk_v1], blocking=block)
    cfg_v2 = GoldenMatchConfig(matchkeys=[mk_v2], blocking=block)
    assert _prep_cache_signature(cfg_v1) == _prep_cache_signature(cfg_v2)
