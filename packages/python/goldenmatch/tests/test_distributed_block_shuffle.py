"""Block-shuffle distributed scoring (issue #844).

These tests need NO Ray runtime: they exercise the pure per-batch helpers
(`_attach_colocation_keys`, `_score_colocated_groups`) and the env gate
directly, simulating the shuffle with an in-process `pl.concat`. That is
exactly the co-location the Ray `repartition(keys=...)` produces, so the
helpers under test are the load-bearing logic regardless of Ray.

The headline test reproduces the bug (legacy per-partition scoring misses a
duplicate split across partitions) AND proves the fix (block-shuffle recovers
it) using the REAL scoring kernel.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _cfg() -> GoldenMatchConfig:
    """One weighted matchkey on `last_name` (jaro_winkler) blocked on `last_name`.

    Identical-surname pairs score 1.0 (>= 0.5 threshold), so a pair is emitted
    iff the two records are co-located in the same scored block.
    """
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="lastname_fuzzy", type="weighted", threshold=0.5,
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])]),
        backend="bucket",
    )


def _data() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Two arbitrary partitions that SPLIT both duplicate pairs across the
    boundary: (0,1) share surname Smith, (2,3) share Jones, but each partition
    holds exactly one member of each pair."""
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "first_name": ["Alice", "Alyce", "Bob", "Robert"],
        "last_name":  ["Smith", "Smith", "Jones", "Jones"],
    })
    p1 = df.filter(pl.col("__row_id__").is_in([0, 2]))  # Alice Smith, Bob Jones
    p2 = df.filter(pl.col("__row_id__").is_in([1, 3]))  # Alyce Smith, Robert Jones
    return p1, p2


def _canon(pairs) -> set[tuple[int, int]]:
    return {(min(a, b), max(a, b)) for a, b, _s in pairs}


# ── env gate ─────────────────────────────────────────────────────────


def test_block_shuffle_disabled_by_default(monkeypatch):
    from goldenmatch.distributed.scoring import _block_shuffle_enabled

    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", raising=False)
    assert _block_shuffle_enabled() is False
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "1")
    assert _block_shuffle_enabled() is True
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "0")
    assert _block_shuffle_enabled() is False


def test_has_colocation_plan():
    from goldenmatch.distributed.scoring import _has_colocation_plan

    assert _has_colocation_plan(_cfg()) is True

    # Empty config: no matchkeys, no blocking -> nothing to key on. (A weighted
    # matchkey can't exist without blocking -- pydantic enforces that -- so the
    # degenerate config is the only real no-plan case.)
    assert _has_colocation_plan(GoldenMatchConfig()) is False


# ── co-location ──────────────────────────────────────────────────────


def test_attach_colocation_keys_colocates_split_duplicates():
    """A duplicate pair split across partitions gets the SAME
    (__keyid__, __block_key__) once exploded, so the shuffle co-locates them."""
    from goldenmatch.distributed.scoring import _attach_colocation_keys

    cfg = _cfg()
    p1, p2 = _data()
    exploded = pl.concat(
        [_attach_colocation_keys(p1, cfg), _attach_colocation_keys(p2, cfg)],
        how="vertical_relaxed",
    )

    def key_of(rid: int) -> tuple:
        return exploded.filter(pl.col("__row_id__") == rid).select(
            ["__keyid__", "__block_key__"]
        ).row(0)

    assert key_of(0) == key_of(1)   # both Smith
    assert key_of(2) == key_of(3)   # both Jones
    assert key_of(0) != key_of(2)   # Smith != Jones


# ── bug + fix, real scoring kernel ───────────────────────────────────


def test_legacy_per_partition_misses_cross_partition_pairs():
    """Reproduces #844: scoring each arbitrary partition in isolation never
    compares records that landed in different partitions."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    cfg = _cfg()
    p1, p2 = _data()
    legacy = _canon(
        _score_partition_with_config(p1, cfg) + _score_partition_with_config(p2, cfg)
    )
    assert (0, 1) not in legacy
    assert (2, 3) not in legacy


def test_block_shuffle_recovers_cross_partition_pairs():
    """The fix: after the block-key shuffle co-locates split duplicates, the
    real scoring kernel recovers BOTH cross-partition pairs."""
    from goldenmatch.distributed.scoring import (
        _attach_colocation_keys,
        _score_colocated_groups,
    )

    cfg = _cfg()
    p1, p2 = _data()
    colocated = pl.concat(
        [_attach_colocation_keys(p1, cfg), _attach_colocation_keys(p2, cfg)],
        how="vertical_relaxed",
    )
    found = _canon(_score_colocated_groups(colocated, cfg))
    assert (0, 1) in found
    assert (2, 3) in found


# ── (b) vectorization parity ─────────────────────────────────────────


def _old_per_group_score(df, config):
    """The pre-#844(b) per-(__keyid__,__block_key__)-group loop, inlined so the
    new single-pass _score_colocated_groups can be proven to emit the identical
    pair set."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    local = config.model_copy()
    local.backend = "bucket"
    pairs = []
    for _k, grp in df.group_by(["__keyid__", "__block_key__"]):
        if grp.height < 2:
            continue
        rec = grp.drop(["__keyid__", "__block_key__"])
        pairs.extend(_score_partition_with_config(rec, local))
    return pairs


def test_score_colocated_groups_parity_with_per_group_loop():
    """#844 (b): the vectorized single-pass _score_colocated_groups emits the
    SAME canonical pair set as the old per-group loop, on a config exercising a
    blocking pass + an EXACT matchkey + a WEIGHTED matchkey, with records that
    co-locate under more than one key (so the explode puts a record in several
    co-location groups). Guards the equivalence the rewrite relies on."""
    from goldenmatch.distributed.scoring import (
        _attach_colocation_keys,
        _score_colocated_groups,
    )

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="email_exact", type="exact",
                fields=[MatchkeyField(field="email")],
            ),
            MatchkeyConfig(
                name="lastname_fuzzy", type="weighted", threshold=0.5,
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
            ),
        ],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])]),
        backend="bucket",
    )
    # 0,1 share surname Smith + email a@x; 4 also shares email a@x (diff surname
    # Lee); 2,3 share surname Jones. So records 0/1/4 co-locate by email AND
    # 0/1 (and 4/5) co-locate by surname -- a record lands in multiple groups.
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "last_name":  ["Smith", "Smith", "Jones", "Jones", "Lee", "Lee"],
        "email":      ["a@x", "a@x", "b@y", "c@z", "a@x", "d@w"],
    })
    colocated = _attach_colocation_keys(df, cfg)

    new_pairs = _canon(_score_colocated_groups(colocated, cfg))
    old_pairs = _canon(_old_per_group_score(colocated, cfg))

    assert new_pairs == old_pairs, (new_pairs, old_pairs)
    # Sanity: both the exact-email and weighted-surname rules contributed.
    assert {(0, 1), (0, 4), (1, 4)}.issubset(new_pairs)  # exact email a@x
    assert (2, 3) in new_pairs                            # weighted surname Jones
