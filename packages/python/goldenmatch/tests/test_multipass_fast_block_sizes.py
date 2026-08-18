"""Measure multi_pass blocking on the full frame instead of extrapolating a sample.

The controller can reason over a FULL-frame measured blocking profile rather
than a sample-extrapolated one -- `_should_measure_blocking`, spec 2026-06-22 --
but only when the config is plain ``static``:

    return is_static and n_rows <= _MEASURE_BLOCKING_MAX_ROWS_LOWER_TIER

`multi_pass` is the common zero-config shape (the #1207 per-identifier union),
so zero-config never qualifies and always extrapolates. The reason for the gate
is cost, and it is real: `_fast_static_block_sizes` bails on any non-static
strategy, so multi_pass falls to the exact ``build_blocks`` loop, which
materializes one frame per block.

Measured, person @ 100,000 rows, the 8-pass zero-config plan:

    exact build_blocks loop      15,953 ms
    per-pass vectorized             762 ms      20.9x

A multi_pass config IS N static passes -- ``_build_multi_pass_blocks``
delegates each one to ``_build_static_blocks`` -- so the same vectorized
group-by applies per pass. That is what this adds.

Why it matters beyond speed: the full-frame measurement of that same plan is

    n_blocks=84,350   reduction_ratio=0.9757   total_comparisons=121,391,850

which is GREEN. The sample-extrapolated profile the controller uses instead
comes back all zeros, rolls up RED, and refuses -- and `rule_low_reduction_ratio`
fires on that same 0.0 default and rewrites the blocking plan. Measuring what is
cheap to measure removes the cause rather than tuning around it.

## Per-pass fallback is required, not optional

`_fast_static_block_sizes` returns None when ANY block is oversized, because
`_build_static_blocks` sub-splits those and the raw group-by sizes would then
diverge. On the person plan exactly one pass (first_name soundex) trips this.
Falling back for the WHOLE config would forfeit the other seven; falling back
per PASS keeps them. So the aggregate must be byte-identical to the exact loop
on a config that mixes both kinds of pass, which is what the parity test below
pins.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import (
    _fast_multi_pass_block_sizes,
    build_blocks,
)
from goldenmatch.core.frame import to_frame


def _mixed_frame(n: int = 600):
    """One column whose blocks stay small, one that goes oversized.

    `tight` has 200 distinct values (3 rows/block, under the cap) so its pass
    vectorizes. `wide` has 3 distinct values (200 rows/block, over the cap of
    50) so its pass must fall back to the exact builder.
    """
    return pa.table({
        "tight": [f"t{i % 200}" for i in range(n)],
        "wide": [f"w{i % 3}" for i in range(n)],
    })


def _config(max_block_size: int = 50) -> BlockingConfig:
    return BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["tight"], transforms=["strip"])],
        passes=[
            BlockingKeyConfig(fields=["tight"], transforms=["strip"]),
            BlockingKeyConfig(fields=["wide"], transforms=["strip"]),
        ],
        max_block_size=max_block_size,
    )


def _exact_sizes(frame, cfg) -> list[int]:
    out = []
    for b in build_blocks(frame, cfg):
        try:
            out.append(b.n_rows())
        except Exception:
            out.append(0)
    return sorted(out)


def test_the_fixture_really_mixes_both_paths():
    """Guard the guard: if neither pass were oversized this would only prove the
    easy case, and the whole point is the mixed config."""
    from goldenmatch.core.blocker import _fast_static_block_sizes

    frame = to_frame(_mixed_frame())
    cfg = _config()
    tight = cfg.model_copy(update={
        "strategy": "static", "keys": [cfg.passes[0]], "passes": None,
    })
    wide = cfg.model_copy(update={
        "strategy": "static", "keys": [cfg.passes[1]], "passes": None,
    })
    assert _fast_static_block_sizes(frame, tight) is not None, "tight must vectorize"
    assert _fast_static_block_sizes(frame, wide) is None, "wide must fall back"


def test_sizes_match_the_exact_builder():
    """The whole justification is that this is the SAME measurement, cheaper."""
    frame = to_frame(_mixed_frame())
    cfg = _config()
    fast = _fast_multi_pass_block_sizes(frame, cfg)
    assert fast is not None
    sizes, _f1, _f2 = fast
    assert sorted(sizes) == _exact_sizes(frame, cfg)


def test_it_declines_on_a_non_multipass_config():
    """Narrow by construction: `static` keeps its own fast path and every other
    strategy keeps the exact loop."""
    frame = to_frame(_mixed_frame())
    cfg = _config().model_copy(update={"strategy": "static", "passes": None})
    assert _fast_multi_pass_block_sizes(frame, cfg) is None


def test_it_declines_when_there_are_no_passes():
    frame = to_frame(_mixed_frame())
    cfg = _config().model_copy(update={"passes": []})
    assert _fast_multi_pass_block_sizes(frame, cfg) is None


def test_an_all_vectorizable_config_still_matches():
    """No oversized pass -- every pass takes the fast path and the aggregate must
    still equal the exact loop."""
    frame = to_frame(_mixed_frame())
    cfg = _config(max_block_size=10_000)
    cfg = cfg.model_copy(update={"passes": [cfg.passes[0]]})
    fast = _fast_multi_pass_block_sizes(frame, cfg)
    assert fast is not None
    assert sorted(fast[0]) == _exact_sizes(frame, cfg)
