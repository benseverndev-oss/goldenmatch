"""Tests for stage instrumentation: build_blocks emits BlockingProfile."""
import polars as pl
import pytest
from goldenmatch.core.profile_emitter import profile_capture
from goldenmatch.core.complexity_profile import BlockingProfile


def _make_test_lf():
    return pl.DataFrame({
        "__row_id__": list(range(20)),
        "name": ["alice"] * 5 + ["bob"] * 5 + ["carol"] * 5 + ["dan"] * 5,
        "__source__": ["x"] * 20,
    }).lazy()


def test_build_blocks_emits_blocking_profile():
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    cfg = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])],
        max_block_size=5000, skip_oversized=False,
    )
    with profile_capture() as e:
        blocks = build_blocks(_make_test_lf(), cfg)
    assert e.blocking is not None
    assert isinstance(e.blocking, BlockingProfile)
    assert e.blocking.n_blocks == 4
    assert e.blocking.keys_used == [["name"]]
    assert e.blocking.singleton_block_count == 0
    assert e.blocking.block_sizes_max == 5


def test_build_blocks_no_emit_when_no_capture():
    """Behavior unchanged when no capture is active — emitter is null singleton."""
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    cfg = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])],
        max_block_size=5000, skip_oversized=False,
    )
    blocks = build_blocks(_make_test_lf(), cfg)  # must not raise; no profile_capture
    assert len(blocks) == 4


def test_build_blocks_emits_singleton_count():
    """Each unique value -> singleton block; emitted count matches."""
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    lf = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4],
        "name": ["a", "b", "c", "d", "e"],  # 5 distinct values
        "__source__": ["x"] * 5,
    }).lazy()
    cfg = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])],
        max_block_size=5000, skip_oversized=False,
    )
    with profile_capture() as e:
        build_blocks(lf, cfg)
    # All blocks are singletons (size 1) so they are filtered out by build_blocks (< 2 records)
    # n_blocks == 0 and singleton_block_count == 0 when all blocks have size < 2
    assert e.blocking is not None
    assert e.blocking.n_blocks == 0
    assert e.blocking.singleton_block_count == 0


def test_scorer_emits_scoring_profile_via_dedupe_df():
    """After fuzzy scoring runs, the emitter holds a ScoringProfile."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.profile_emitter import profile_capture
    from goldenmatch.core.complexity_profile import ScoringProfile
    import goldenmatch as gm

    df = pl.DataFrame({
        "name": ["alice", "alyce", "bob", "bobby", "carol", "carrol"] * 3,
        "city": ["nyc"] * 18,
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                  weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    with profile_capture() as e:
        gm.dedupe_df(df, config=cfg)
    assert e.scoring is not None, "scorer did not emit a ScoringProfile"
    assert isinstance(e.scoring, ScoringProfile)
    assert e.scoring.n_pairs_scored > 0
    assert sum(e.scoring.score_histogram) == e.scoring.n_pairs_scored
    assert 0.0 <= e.scoring.dip_statistic <= 0.25
    assert 0.0 <= e.scoring.mass_above_threshold <= 1.0
    assert 0.0 <= e.scoring.mass_in_borderline <= 1.0
