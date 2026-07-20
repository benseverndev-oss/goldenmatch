"""FS EM block-slim projection (`GOLDENMATCH_FS_EM_BLOCK_SLIM`, default ON).

EM training reads ONLY the ``__row_id__`` column of the blocks it samples
(``probabilistic._sample_blocked_pairs_with_fields``); the sampled pairs' field
values are looked up on the full ``score_frame``, never the block frames. So
``build_blocks`` materializing every block at full source width -- across every
multi_pass/SN pass -- is pure waste (the FS memory PEAK, ~1.4 GB at 100k
person). ``pipeline._build_em_blocks`` projects the EM block-frame to
``__row_id__`` + the blocking group columns first.

This locks:
  1. Gate resolution (default ON; ``0``/``false``/``off`` restores full width).
  2. The EM path hands ``build_blocks`` a NARROWER frame when slim is on, and it
     still carries ``__row_id__`` + the blocking fields.
  3. Output is byte-identical slim-on vs slim-off (EM only reads ``__row_id__``).
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
)
from goldenmatch.core.pipeline import _fs_em_block_slim_enabled

from tests.test_probabilistic import _make_dedupe_df, _make_probabilistic_mk


def _cfg(**kwargs) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[_make_probabilistic_mk()],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        **kwargs,
    )


def test_gate_default_on_and_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", raising=False)
    assert _fs_em_block_slim_enabled() is True
    for off in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", off)
        assert _fs_em_block_slim_enabled() is False
    for on in ("1", "true", "yes", "anything"):
        monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", on)
        assert _fs_em_block_slim_enabled() is True


def _wide_frame() -> pl.DataFrame:
    """A frame shaped like the prepared EM input: __row_id__, a blocking key
    column, plus non-blocking source + __xform_* columns EM never reads."""
    return pl.DataFrame(
        {
            "__row_id__": list(range(1, 7)),
            "zip": ["90210", "90210", "10001", "10001", "60601", "60601"],
            "first_name": ["A", "B", "C", "D", "E", "F"],
            "__xform_first_name_x__": ["a", "b", "c", "d", "e", "f"],
            "__xform_zip_y__": ["90210", "90210", "10001", "10001", "60601", "60601"],
        }
    )


def _spy_build_blocks_names(monkeypatch) -> list:
    from goldenmatch.core import pipeline as pipeline_mod

    captured: list = []
    real = pipeline_mod.build_blocks

    def _spy(lf, config):
        captured.append(
            list(getattr(lf, "column_names", None) or getattr(lf, "columns", []))
        )
        return real(lf, config)

    monkeypatch.setattr(pipeline_mod, "build_blocks", _spy)
    return captured


def test_slim_projects_to_row_id_plus_blocking_fields(monkeypatch):
    from goldenmatch.core.pipeline import _build_em_blocks

    monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", "0")  # exercise the slim path
    monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", "1")
    names = _spy_build_blocks_names(monkeypatch)

    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    _build_em_blocks(_wide_frame(), blocking)

    assert len(names) == 1
    got = names[0]
    assert "__row_id__" in got and "zip" in got  # row id + blocking group col
    assert "first_name" not in got  # non-blocking source column dropped
    assert not any(n.startswith("__xform_") for n in got)  # xform cols dropped


def test_slim_off_keeps_full_width(monkeypatch):
    from goldenmatch.core.pipeline import _build_em_blocks

    monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", "0")  # exercise the slim path
    monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", "0")
    names = _spy_build_blocks_names(monkeypatch)

    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    _build_em_blocks(_wide_frame(), blocking)

    assert len(names) == 1
    got = names[0]
    assert "__row_id__" in got
    assert any(n.startswith("__xform_") for n in got)  # full width retained


def _partitions(result) -> list[tuple[int, ...]]:
    return sorted(
        tuple(sorted(c["members"]))
        for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    )


def test_slim_output_parity(monkeypatch):
    """EM reads only __row_id__, so slim on/off must produce identical clusters."""
    from goldenmatch import dedupe_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", "0")  # compare slim on vs off
    df = _make_dedupe_df().drop("__row_id__")

    monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", "1")
    on = dedupe_df(df, config=_cfg(backend="bucket"))
    monkeypatch.setenv("GOLDENMATCH_FS_EM_BLOCK_SLIM", "0")
    off = dedupe_df(df, config=_cfg(backend="bucket"))

    assert _partitions(on) == _partitions(off)
