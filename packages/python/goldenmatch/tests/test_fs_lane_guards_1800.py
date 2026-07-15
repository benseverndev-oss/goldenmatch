"""Issue #1800 — FS (probabilistic) matchkeys must fail loudly in the
distributed and chunked lanes, not silently contribute zero pairs.

Both lanes score only ``exact`` + ``weighted`` matchkeys per partition /
chunk. A ``type="probabilistic"`` matchkey routed through them used to be
skipped with no error and no log, so a config that works single-box lost
its Fellegi-Sunter scoring the moment it crossed into either lane (silent
wrong results). These tests assert the loud ``NotImplementedError`` guard,
matching the DataFusion backend's posture and Sail's documented one-box
fallback.

Deliberately NOT gated on ``ray`` — every guard fires on the driver before
any Ray work, so the tests run in the default (ray-free) environment.
"""

from __future__ import annotations

import csv

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)


def _fs_config() -> GoldenMatchConfig:
    """A config with a probabilistic (Fellegi-Sunter) matchkey + blocking."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fs_name",
                type="probabilistic",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler")],
            ),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        output=OutputConfig(),
    )


def _weighted_config() -> GoldenMatchConfig:
    """A weighted config (the lanes DO support this) — negative control."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="w_name",
                type="weighted",
                threshold=0.8,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
                ],
            ),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        output=OutputConfig(),
    )


def _person_df() -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["Alice", "Alyce", "Bob", "Robert"],
        "zip": ["10001", "10001", "10002", "10002"],
    })


# ── Distributed kernel (`_score_partition_with_config`) ────────────────


def test_kernel_rejects_probabilistic_matchkey():
    """The narrow scoring kernel must raise, not silently return []. This is
    the cited buggy loop (``if mk.type != 'weighted': continue``)."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    with pytest.raises(NotImplementedError, match="probabilistic"):
        _score_partition_with_config(_person_df(), _fs_config())


def test_kernel_error_names_the_matchkey_and_alternative():
    """The message must name the offending matchkey and point at the
    single-box alternative so a user can act on it."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    with pytest.raises(NotImplementedError) as exc:
        _score_partition_with_config(_person_df(), _fs_config())
    msg = str(exc.value)
    assert "fs_name" in msg
    assert "weighted" in msg  # the conversion suggestion


def test_kernel_still_accepts_weighted():
    """Negative control: a weighted config must NOT trip the guard."""
    from goldenmatch.core.pipeline import _score_partition_with_config

    cfg = _weighted_config()
    cfg.backend = "bucket"
    pairs = _score_partition_with_config(_person_df(), cfg)
    assert isinstance(pairs, list)  # runs to completion, no raise


# ── Distributed driver entry (`score_blocks_distributed`) ──────────────


def test_score_blocks_distributed_rejects_probabilistic():
    """Driver-side guard: must raise BEFORE dispatch (no Ray touched), so
    the failure isn't swallowed by the per-partition try/except."""
    from goldenmatch.distributed.scoring import score_blocks_distributed

    # df_ds is never touched — the guard reads only the config.
    with pytest.raises(NotImplementedError, match="probabilistic"):
        score_blocks_distributed(None, _fs_config())


# ── Chunked lane (`ChunkedMatcher.process_file`) ───────────────────────


def test_chunked_process_file_rejects_probabilistic(tmp_path):
    from goldenmatch.core.chunked import ChunkedMatcher

    f = tmp_path / "data.csv"
    with open(f, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "zip"])
        for i in range(10):
            w.writerow([f"Person {i}", f"{10000 + i % 3}"])

    matcher = ChunkedMatcher(config=_fs_config(), chunk_size=5)
    with pytest.raises(NotImplementedError, match="probabilistic"):
        matcher.process_file(str(f))


def test_chunked_process_file_still_accepts_weighted(tmp_path):
    """Negative control: weighted config runs through the chunked lane."""
    from goldenmatch.core.chunked import ChunkedMatcher

    f = tmp_path / "data.csv"
    with open(f, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "zip"])
        for i in range(10):
            w.writerow([f"Person {i}", f"{10000 + i % 3}"])

    matcher = ChunkedMatcher(config=_weighted_config(), chunk_size=5)
    result = matcher.process_file(str(f))  # no raise
    assert result["total_records"] == 10


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
