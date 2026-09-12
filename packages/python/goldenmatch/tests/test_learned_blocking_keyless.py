"""The >=50K learned-blocking upgrade must not overwrite a key-less blocking plan.

`_legacy_auto_configure_v0` (the heuristic the zero-config controller calls) sets
`blocking.strategy = "learned"` at `total_rows >= 50_000`. Learned blocking's first pass
is static blocking on `blocking.keys`, run on a sample to find training pairs. A key-less
plan -- the token / lsh / simhash plans `_text_corpus_blocking` returns for free text --
has no keys, so that pass builds no blocks and finds no training pairs, and the "falling
back to static blocking" that follows uses the same empty keys. The run ends with zero
blocks, zero candidate pairs and zero matches, and raises nothing.

Measured on DBLP-Scholar (66,879 rows): zero-config `dedupe_df` returned 0 clusters.
Keeping the token plan returns 43,957 scored pairs (F1 0.0 -> 0.189).

These tests use a small frame with an `n_rows_full` override so the >=50K branch fires
without materializing 50K rows, the same way tests/test_blocking_union_learned_1316.py does.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, LSHKeyConfig, TokenBlockingConfig
from goldenmatch.core.autoconfig import _legacy_auto_configure_v0
from goldenmatch.core.blocker import build_blocks

_LARGE = 60_000  # >= the 50K learned-blocking gate

_BASES = (
    "entity resolution at scale with learned blocking",
    "probabilistic record linkage for bibliographic data",
    "fast approximate string matching over large corpora",
    "a survey of duplicate detection in databases",
    "scalable clustering of noisy author names",
)


def _titles_df(n: int = 1000) -> pl.DataFrame:
    return pl.DataFrame({
        "title": [f"{_BASES[i % len(_BASES)]} part {i % 300}" for i in range(n)],
        "venue": [f"venue {i % 40}" for i in range(n)],
        "year": [str(1990 + i % 30) for i in range(n)],
    })


def _token_plan() -> BlockingConfig:
    return BlockingConfig(
        strategy="token",
        token=TokenBlockingConfig(column="title", min_token_length=3, max_df_ratio=0.02),
    )


def _lsh_plan() -> BlockingConfig:
    return BlockingConfig(
        strategy="lsh",
        lsh=LSHKeyConfig(column="title", mode="word", k=2, num_perms=128, threshold=0.5, seed=0),
    )


_KEYLESS_PLANS = {"token": _token_plan, "lsh": _lsh_plan}


def _block_count(df: pl.DataFrame, blocking: BlockingConfig) -> int:
    return len(build_blocks(df.with_row_index("__row_id__"), blocking))


@pytest.mark.parametrize("strategy", sorted(_KEYLESS_PLANS))
def test_learned_blocking_on_a_keyless_plan_builds_no_blocks(strategy: str) -> None:
    """Why the upgrade has to skip these plans: the same plan re-labelled `learned`
    builds nothing, while the plan as chosen builds blocks."""
    df = _titles_df()
    plan = _KEYLESS_PLANS[strategy]()
    assert plan.keys == []
    assert _block_count(df, plan) > 0
    relabelled = plan.model_copy(update={"strategy": "learned"})
    assert _block_count(df, relabelled) == 0


@pytest.mark.parametrize("strategy", sorted(_KEYLESS_PLANS))
def test_keyless_plan_survives_the_learned_upgrade(monkeypatch, strategy: str) -> None:
    """THE REGRESSION. Before the fix the >=50K gate turned these plans into learned
    blocking with no keys, and zero-config dedupe found nothing."""
    df = _titles_df()
    monkeypatch.setattr(
        "goldenmatch.core.autoconfig.build_blocking",
        lambda *a, **k: _KEYLESS_PLANS[strategy](),
    )
    cfg = _legacy_auto_configure_v0(df, n_rows_full=_LARGE)
    assert cfg.blocking is not None
    assert cfg.blocking.strategy == strategy, (
        f"the >=50K gate replaced a key-less {strategy} plan with {cfg.blocking.strategy!r}"
    )
    assert _block_count(df, cfg.blocking) > 0
