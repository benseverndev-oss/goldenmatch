"""GOLDENMATCH_FS_SIGNATURE_PRUNE wiring: where the pruner is allowed to exist,
and that EM's training sample actually passes through it."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import pipeline as P
from goldenmatch.core import signature_prune as S

ENV = S.ENV


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _world_frame(n_entities=400, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(n_entities):
        nm, d, z = int(rng.integers(30)), int(rng.integers(300)), int(rng.integers(300))
        for c in range(2 if rng.random() < 0.5 else 1):
            dup = c == 1
            rows.append({
                "name": f"n{int(rng.integers(30, 60)) if dup and rng.random() < 0.3 else nm}",
                "dob": f"d{int(rng.integers(300)) if dup and rng.random() < 0.2 else d}",
                "zip": f"z{int(rng.integers(300)) if dup and rng.random() < 0.2 else z}",
            })
    return pl.DataFrame(rows).with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64)
    )


def _blocking():
    return BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["name"], transforms=[]),
            BlockingKeyConfig(fields=["name"], transforms=["lowercase"]),
            BlockingKeyConfig(fields=["dob"], transforms=[]),
            BlockingKeyConfig(fields=["zip"], transforms=[]),
        ],
    )


def _fit(**overrides):
    kw = {
        "use_bucket": True,
        "target_ids": None,
        "across_files_only": False,
        "bench_dump_dir": None,
    }
    kw.update(overrides)
    cfg = SimpleNamespace(blocking=_blocking())
    return P._fit_fs_signature_pruner(cfg, overrides.pop("frame", _world_frame()), **kw)  # type: ignore[arg-type]


def test_unset_never_touches_the_frame():
    cfg = SimpleNamespace(blocking=_blocking())
    got = P._fit_fs_signature_pruner(
        cfg, object(), use_bucket=True, target_ids=None,  # type: ignore[arg-type]
        across_files_only=False, bench_dump_dir=None,
    )
    assert got is None


def test_fits_on_the_dedupe_bucket_route(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    pruner = _fit()
    assert pruner is not None
    assert pruner.report["rule"] == "dominant"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"target_ids": {1, 2}}, "match / across-files"),
        ({"across_files_only": True}, "match / across-files"),
        ({"use_bucket": False}, "batched / external-blocks"),
        ({"bench_dump_dir": "dump"}, "bench pair dump"),
    ],
)
def test_declines_loudly_where_scored_pairs_are_not_filtered(monkeypatch, caplog, overrides, reason):
    monkeypatch.setenv(ENV, "1")
    with caplog.at_level(logging.WARNING):
        assert _fit(**overrides) is None
    assert "DECLINED" in caplog.text and reason in caplog.text


def test_declines_on_the_out_of_core_route(monkeypatch, caplog):
    import goldenmatch.backends.fs_out_of_core as ooc

    monkeypatch.setenv(ENV, "1")
    monkeypatch.setattr(ooc, "fs_out_of_core_enabled", lambda: True)
    with caplog.at_level(logging.WARNING):
        assert _fit() is None
    assert "out-of-core" in caplog.text


# ---- EM's training sample -------------------------------------------------------

def _mk():
    return MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="dob", scorer="exact", levels=2),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _train(pair_filter, caplog):
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import train_em

    df = _world_frame()
    blocks = build_blocks(df.lazy(), _blocking())
    with caplog.at_level(logging.WARNING):
        return train_em(
            df, _mk(), blocks=blocks, blocking_fields=["name", "dob", "zip"],
            pair_filter=pair_filter,
        )


def test_training_sample_passes_through_the_filter(caplog):
    """KNOWN-POSITIVE that the filter reaches EM: a filter dropping odd-id pairs
    must be called and must shrink the drawn sample."""
    calls = []

    def keep_even(a, b):
        calls.append(len(a))
        return (np.asarray(a) % 2) == 0

    _train(keep_even, caplog)
    assert calls, "EM never consulted the filter"
    assert "candidate filter kept" in caplog.text
    assert "too few" not in caplog.text


def test_a_filter_that_keeps_nothing_falls_back_and_says_so(caplog):
    _train(lambda a, b: np.zeros(len(a), dtype=bool), caplog)
    assert "too few, training on the unfiltered sample" in caplog.text


def test_no_filter_is_byte_identical(caplog):
    base = _train(None, caplog)
    again = _train(None, caplog)
    assert base.m_probs == again.m_probs
    assert "candidate filter" not in caplog.text
