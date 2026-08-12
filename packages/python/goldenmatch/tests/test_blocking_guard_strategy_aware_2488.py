"""The #417 degenerate-blocking guard must not condemn a self-configured
blocking strategy for having no `keys` (#2488).

`BlockingConfig` REJECTS `keys` alongside `token`/`lsh`/`simhash`, so those
plans always have `keys == []`. The guard inferred "no blocking configured"
from that emptiness alone, which (a) marked a working plan's blocking
sub-profile RED and (b) sent the block-size estimator off to measure the
MATCHKEY fields -- judging a blocking plan that was not the configured one.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    KEYS_DRIVEN_BLOCKING_STRATEGIES,
    BlockingConfig,
    BlockingKeyConfig,
    LSHKeyConfig,
    TokenBlockingConfig,
)
from goldenmatch.core.autoconfig_controller import (
    _blocking_is_keyless,
    _carries_own_blocking_plan,
)

# ---- the constant is the single source, and matches the validator ----


def test_keys_driven_set_is_exactly_the_validator_s_view():
    """`_validate_keys_or_passes` requires keys for static/adaptive and
    keys-or-passes for multi_pass, and requires NOTHING of the rest. The
    constant must not drift from that."""
    for strategy in KEYS_DRIVEN_BLOCKING_STRATEGIES:
        with pytest.raises(ValueError):
            BlockingConfig(strategy=strategy, keys=[], auto_suggest=False)


def test_every_strategy_is_classified():
    """A new strategy must land on one side deliberately, not by omission."""
    import typing
    all_strategies = set(typing.get_args(BlockingConfig.model_fields["strategy"].annotation))
    assert KEYS_DRIVEN_BLOCKING_STRATEGIES <= all_strategies


# ---- _carries_own_blocking_plan ----


def test_self_configured_strategies_carry_their_own_plan():
    for cfg in (
        BlockingConfig(strategy="token", token=TokenBlockingConfig(column="t")),
        BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="t", threshold=0.5)),
        BlockingConfig(strategy="learned"),
    ):
        assert _carries_own_blocking_plan(cfg), cfg.strategy


def test_keys_driven_strategies_do_not():
    cfg = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])])
    assert not _carries_own_blocking_plan(cfg)


def test_none_blocking_carries_nothing():
    assert not _carries_own_blocking_plan(None)


# ---- _blocking_is_keyless: the actual guard predicate ----


def test_a_token_plan_is_not_keyless():
    """The regression. `keys == []` is mandatory for a token config -- the
    validator rejects keys alongside `token` -- so reading emptiness as
    "no blocking" condemns every token plan on sight."""
    cfg = BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title"))
    assert cfg.keys == []
    assert not _blocking_is_keyless(cfg)


def test_an_lsh_plan_is_not_keyless():
    cfg = BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="t", threshold=0.5))
    assert not _blocking_is_keyless(cfg)


def test_a_static_config_with_no_keys_is_still_keyless():
    """The case the guard exists for must keep firing: autoconfig's
    `_degenerate_blocking_config()` shape."""
    cfg = BlockingConfig(strategy="static", keys=[], auto_suggest=True)
    assert _blocking_is_keyless(cfg)


def test_a_static_config_with_keys_is_not_keyless():
    cfg = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["a"])])
    assert not _blocking_is_keyless(cfg)


def test_missing_blocking_is_keyless():
    assert _blocking_is_keyless(None)


def test_keys_win_over_strategy():
    """multi_pass with real passes is configured, whichever way it got there."""
    cfg = BlockingConfig(strategy="multi_pass", keys=[BlockingKeyConfig(fields=["a"])])
    assert not _blocking_is_keyless(cfg)
