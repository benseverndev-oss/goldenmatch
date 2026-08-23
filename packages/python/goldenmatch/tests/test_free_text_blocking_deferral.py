"""#2717: a prefix key on free text must defer to the block analyzer.

`build_blocking` only emits static/compound keys. On free text that means a
prefix, and `block_analyzer.free_text_columns` states the problem outright:
"a prefix key on a product title is a near-useless block."

Measured on real Amazon-Google against `Amzon_GoogleProducts_perfectMapping.csv`:

    committed `multi_pass` on ['title','manufacturer']  blocking recall 0.0408
    deferred  -> strategy=token, 94,938 pairs           blocking recall 0.9531

Token blocking was never broken -- it was UNREACHABLE. Two locks: `auto_suggest`
defaults to False so `_run_auto_suggest` returns immediately, and its token
branch sits behind `if not config.blocking.keys`, which auto-config has already
populated. `build_token_blocks` never logged because nothing set
`strategy="token"`, which `block_analyzer.py:140` misread as a broken
integration. Invoked directly it produces 2,754 blocks.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")

from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig  # noqa: E402
from goldenmatch.core.autoconfig import defer_free_text_blocking_to_analyzer  # noqa: E402


def _tbl(titles, extra=None):
    d = {"id": [f"r{i}" for i in range(len(titles))], "title": titles}
    if extra:
        d.update(extra)
    return pa.table(d)


_LONG = [f"apple ipod touch {i} gb portable media player silver model" for i in range(40)]
_SHORT = [f"john smith {i}" for i in range(40)]


def test_free_text_key_defers_to_the_analyzer():
    cfg = BlockingConfig(strategy="multi_pass", keys=[BlockingKeyConfig(fields=["title"])])
    out = defer_free_text_blocking_to_analyzer(cfg, _tbl(_LONG))
    assert out.auto_suggest is True
    assert not out.keys


def test_a_compound_with_one_free_text_field_still_defers():
    """ANY, not EVERY: compounding a free-text prefix with a short field does
    not rescue it -- ['title','manufacturer'] measures 0.0408 recall."""
    t = _tbl(_LONG, {"manufacturer": ["apple"] * 40})
    cfg = BlockingConfig(strategy="multi_pass",
                         keys=[BlockingKeyConfig(fields=["title", "manufacturer"])])
    out = defer_free_text_blocking_to_analyzer(cfg, t)
    assert out.auto_suggest is True and not out.keys


def test_short_text_is_untouched():
    """THE guard. Names sit at 2-4 tokens; name/address blocking must not move."""
    cfg = BlockingConfig(strategy="multi_pass", keys=[BlockingKeyConfig(fields=["title"])])
    out = defer_free_text_blocking_to_analyzer(cfg, _tbl(_SHORT))
    assert out.auto_suggest is False
    assert [k.fields for k in out.keys] == [["title"]]


def test_a_deliberate_strategy_is_not_overridden():
    """canopy/learned were chosen for reasons this does not model.

    (ann/lsh need their own sub-config to even construct, so the two that
    validate bare are enough to pin the branch.)"""
    from goldenmatch.config.schemas import CanopyConfig

    canopy = BlockingConfig(
        strategy="canopy", keys=[BlockingKeyConfig(fields=["title"])],
        canopy=CanopyConfig(fields=["title"], loose_threshold=0.3, tight_threshold=0.7),
    )
    learned = BlockingConfig(strategy="learned", keys=[BlockingKeyConfig(fields=["title"])])
    for cfg in (canopy, learned):
        out = defer_free_text_blocking_to_analyzer(cfg, _tbl(_LONG))
        assert out.strategy == cfg.strategy, cfg.strategy
        assert out.auto_suggest is False, cfg.strategy


def test_already_deferred_config_is_left_alone():
    cfg = BlockingConfig(auto_suggest=True)
    assert defer_free_text_blocking_to_analyzer(cfg, _tbl(_LONG)) is cfg


def test_a_none_config_passes_through():
    assert defer_free_text_blocking_to_analyzer(None, _tbl(_LONG)) is None


def test_the_deferred_config_is_itself_valid():
    """`static`/`multi_pass` normally REQUIRE keys, and this empties them.

    That is legal only because the validator exempts auto_suggest
    ("auto_suggest discovers keys at runtime", schemas.py:1283). If that
    exemption ever goes, this fix produces an invalid config -- so pin it by
    re-validating the result rather than trusting `model_copy` to skip checks.
    """
    cfg = BlockingConfig(strategy="multi_pass", keys=[BlockingKeyConfig(fields=["title"])])
    out = defer_free_text_blocking_to_analyzer(cfg, _tbl(_LONG))
    BlockingConfig.model_validate(out.model_dump())


def test_it_fails_open():
    """Advisory only -- a broken frame must not break config generation."""
    cfg = BlockingConfig(strategy="multi_pass", keys=[BlockingKeyConfig(fields=["title"])])
    assert defer_free_text_blocking_to_analyzer(cfg, object()) is cfg
