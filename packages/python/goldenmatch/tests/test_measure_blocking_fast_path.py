"""Parity + behavior tests for the Stage-D fast path in
``measure_blocking_profile`` (spec 2026-06-22).

The fast path (`_fast_static_block_sizes`) computes the block-size distribution
with a single vectorized ``group_by`` instead of building + re-collecting every
block. These tests pin it to be BYTE-IDENTICAL to the exact ``build_blocks``
fallback across the configs where it is meant to fire, and to correctly BAIL
(return None → fallback) where it must not.
"""
from __future__ import annotations

import dataclasses
import random
import types

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
)
from goldenmatch.core import blocker
from goldenmatch.core.blocker import (
    _fast_static_block_sizes,
    measure_blocking_profile,
)

_SURNAMES = [
    "smith", "jones", "brown", "davis", "wilson", "taylor", "thomas", "moore",
    "jackson", "white", "harris", "martin", "thompson", "garcia", "martinez",
]
_FIRST = ["james", "mary", "john", "patricia", "robert", "jennifer"]


def _person_df(n: int, seed: int = 0) -> pl.DataFrame:
    rng = random.Random(seed)
    return pl.DataFrame(
        {
            "first_name": [rng.choice(_FIRST) for _ in range(n)],
            "last_name": [rng.choice(_SURNAMES) for _ in range(n)],
            "zip": [f"{rng.randint(10000, 99999)}" for _ in range(n)],
        }
    )


def _cfg(**blocking_kwargs) -> GoldenMatchConfig:
    cfg = GoldenMatchConfig()
    cfg.blocking = BlockingConfig(**blocking_kwargs)
    return cfg


def _measure_via_fallback(df: pl.DataFrame, cfg: GoldenMatchConfig):
    """Force the exact build_blocks path by stubbing the fast path to bail."""
    orig = blocker._fast_static_block_sizes
    blocker._fast_static_block_sizes = lambda lf, config: None
    try:
        return measure_blocking_profile(df, cfg)
    finally:
        blocker._fast_static_block_sizes = orig


def _asdict_no_chao1(profile) -> dict:
    """asdict() minus the Chao1 fields. The fast static path measures
    chao1_f1/chao1_f2 (S1); the exact build_blocks fallback can't recover the
    pre-singleton-drop counts and leaves them None. Every OTHER field must stay
    byte-identical between the two paths, which is what these parity tests pin."""
    return {
        k: v
        for k, v in dataclasses.asdict(profile).items()
        if k not in ("chao1_f1", "chao1_f2")
    }


# ---- configs where the fast path MUST fire and match the fallback exactly ----

_PARITY_CONFIGS = {
    "single_key": dict(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=1_000_000,
        skip_oversized=False,
    ),
    "key_with_transform": dict(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"])],
        max_block_size=1_000_000,
        skip_oversized=False,
    ),
    "compound_key": dict(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name", "zip"], transforms=[])],
        max_block_size=1_000_000,
        skip_oversized=False,
    ),
    "multi_key": dict(
        strategy="static",
        keys=[
            BlockingKeyConfig(fields=["last_name"], transforms=[]),
            BlockingKeyConfig(fields=["zip"], transforms=[]),
        ],
        max_block_size=1_000_000,
        skip_oversized=False,
    ),
    # NOTE: an oversized-block config is intentionally NOT here -- since #372
    # (default-path auto-split), oversized blocks are sub-split under BOTH
    # skip_oversized values, so the fast path bails to the exact loop rather than
    # trusting the raw group-by sizes. That bail is asserted in
    # test_bails_on_oversized_split below.
}


@pytest.mark.parametrize("name", sorted(_PARITY_CONFIGS))
def test_fast_path_byte_identical_to_fallback(name: str) -> None:
    df = _person_df(2000, seed=7)
    cfg = _cfg(**_PARITY_CONFIGS[name])

    # The fast path must actually fire for these configs (not silently bail).
    assert _fast_static_block_sizes(df.lazy(), cfg.blocking) is not None, (
        f"{name}: fast path unexpectedly bailed"
    )

    fast = measure_blocking_profile(df, cfg)
    slow = _measure_via_fallback(df, cfg)
    assert fast is not None and slow is not None
    assert _asdict_no_chao1(fast) == _asdict_no_chao1(slow), (
        f"{name}: fast/fallback BlockingProfile diverged"
    )
    # The fast path additionally measures Chao1 inputs; the fallback leaves None.
    assert slow.chao1_f1 is None and slow.chao1_f2 is None


# ---- configs where the fast path MUST bail (return None) to stay correct ----

def test_bails_on_non_static_strategy() -> None:
    cfg = _cfg(
        strategy="sorted_neighborhood",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=1_000_000,
    )
    assert _fast_static_block_sizes(_person_df(50).lazy(), cfg.blocking) is None


@pytest.mark.parametrize("skip_oversized", [True, False])
def test_bails_on_oversized_split(skip_oversized: bool) -> None:
    # Oversized blocks are sub-split under BOTH skip_oversized values now
    # (True => ANN/auto-split/skip; False => zero-config auto-split, #372), so
    # the raw group-by sizes diverge from the built blocks => must bail so the
    # exact path runs.
    cfg = _cfg(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=10,
        skip_oversized=skip_oversized,
    )
    assert _fast_static_block_sizes(_person_df(2000).lazy(), cfg.blocking) is None


def test_bails_on_no_keys() -> None:
    # BlockingConfig(strategy="static") validates that keys is non-empty, so the
    # empty-keys guard only protects callers passing a config with keys=None/[].
    # Exercise it with a lightweight stub (bypasses pydantic validation).
    stub = types.SimpleNamespace(
        strategy="static", keys=[], auto_select=False,
        max_block_size=1_000_000, skip_oversized=False,
    )
    assert _fast_static_block_sizes(_person_df(50).lazy(), stub) is None


def test_drops_singletons_like_build_blocks() -> None:
    # Every last_name unique => all blocks size 1 => dropped => no blocks.
    df = pl.DataFrame({"last_name": [f"name_{i}" for i in range(200)]})
    cfg = _cfg(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=1_000_000,
        skip_oversized=False,
    )
    sizes, f1, f2 = _fast_static_block_sizes(df.lazy(), cfg.blocking)
    assert sizes == []
    assert f1 == 200  # every key unique -> 200 singleton blocks (S1 Chao1 F1)
    assert f2 == 0
    prof = measure_blocking_profile(df, cfg)
    assert prof is not None
    assert prof.n_blocks == 0
    assert prof.total_comparisons == 0
    assert prof.chao1_f1 == 200
    assert prof.chao1_f2 == 0


def test_null_and_sentinel_keys_dropped() -> None:
    # Real None plus stringy sentinels must not form a block (parity with the
    # _build_static_blocks null/sentinel filter).
    df = pl.DataFrame(
        {
            "last_name": [
                "smith", "smith", "smith",   # one real block of 3
                None, None,                  # real nulls -> dropped
                "NaN", "nan",                # sentinels -> dropped
            ]
        }
    )
    cfg = _cfg(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=1_000_000,
        skip_oversized=False,
    )
    fast = measure_blocking_profile(df, cfg)
    slow = _measure_via_fallback(df, cfg)
    assert _asdict_no_chao1(fast) == _asdict_no_chao1(slow)
    assert fast.total_comparisons == 3  # C(3,2)
