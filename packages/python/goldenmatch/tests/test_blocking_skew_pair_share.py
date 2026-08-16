"""``BlockingProfile.health`` grades skew by WORK CONCENTRATION, not by a size percentile.

## Why this file exists

The old skew rule was ``block_sizes_p99 > 10 * (n_rows / n_blocks)``. Its
denominator is the MEAN block size, which is pinned near 1 whenever blocking is
fine-grained (``n_blocks -> n_rows``), so the bar it sets collapses toward 10
rows no matter how healthy the layout is. Measured on the head-to-head shapes at
100,000 rows (``scripts/bench_er_headtohead/diagnose_zeroconfig_refusal.py``,
run 31976392050):

    person   n_blocks=84,293  avg=1.19  p99=72   p99/avg=60.69  -> RED (fired)
             reduction=0.9757  singletons=0
             total_comparisons=121,372,923  largest block pairs=2,353,365
             >> largest block owns 1.9% of all candidate pairs

    biblio   n_blocks=22,151  avg=4.51  p99=39   p99/avg= 8.64  -> GREEN
             reduction=0.9996  singletons=0
             total_comparisons=1,770,258  largest block pairs=2,415
             >> largest block owns 0.14% of all candidate pairs

person graded RED -- and at ``n_rows >= REFUSE_AT_N`` a RED blocking profile
makes zero-config REFUSE the run -- while its largest block contributed under
2% of the work. That is not skew. The rule was reading "many small blocks" as
"dangerous tail".

## What skew actually costs

Skew hurts because one block becomes a straggler: within-block pairs are
quadratic in block size, so a single oversized block can own most of the work
and cannot be parallelised away. The honest measure is therefore the LARGEST
block's share of ``total_comparisons``, which these tests pin.

A rejected alternative: the share of work in the top 1% of blocks. It can only
be estimated as a lower bound from the profile (``p99`` is the size floor for
that band, and person's max of 2,170 against a p99 of 72 shows how loose the
bound gets), and a heavy band of mid-sized blocks parallelises fine anyway --
it is a cost question, which ``reduction_ratio`` already owns.
"""
from __future__ import annotations

from goldenmatch.core.complexity_profile import BlockingProfile, HealthVerdict


def test_person_100k_fine_grained_blocking_is_green():
    """The measured person@100k profile. RED today, and it should not be.

    Every number here is copied from the diagnostic run, so this fails on the
    unfixed rule for exactly the reason the shape was refused in production.
    """
    bp = BlockingProfile(
        keys_used=[["last_name", "zip"]],
        n_blocks=84_293,
        total_comparisons=121_372_923,
        reduction_ratio=0.975725,
        block_sizes_p50=2,
        block_sizes_p95=10,
        block_sizes_p99=72,
        block_sizes_max=2_170,
        singleton_block_count=0,
        oversized_block_count=0,
    )
    # 2,170 rows -> 2,353,365 pairs, 1.9% of 121,372,923.
    assert bp.largest_block_pair_share < 0.02
    assert bp.health(n_rows=100_000) == HealthVerdict.GREEN


def test_biblio_100k_stays_green():
    """The control: GREEN under the old rule and under the new one.

    Without this, a rule that graded everything GREEN would pass the suite.
    """
    bp = BlockingProfile(
        keys_used=[["title_prefix"]],
        n_blocks=22_151,
        total_comparisons=1_770_258,
        reduction_ratio=0.999646,
        block_sizes_p50=6,
        block_sizes_p95=31,
        block_sizes_p99=39,
        block_sizes_max=70,
        singleton_block_count=0,
        oversized_block_count=0,
    )
    assert bp.health(n_rows=100_000) == HealthVerdict.GREEN


def test_single_dominating_block_is_red():
    """One block of 10,000 rows inside 100,000 -- 98.5% of all pairs.

    The old rule MISSES this: p99 is 18 (the giant is one block out of 5,000,
    so it sits above the 99th percentile, not at it) against a 10*avg bar of
    200, and reduction_ratio is 0.99 because 50M pairs is still a tiny slice of
    5e9. A percentile cannot see a single straggler; a pair share can.
    """
    bp = BlockingProfile(
        keys_used=[["state"]],
        n_blocks=5_000,
        # 49,995,000 pairs from the giant + ~764,847 from 4,999 blocks of 18.
        total_comparisons=50_759_847,
        reduction_ratio=0.989848,
        block_sizes_p50=18,
        block_sizes_p95=18,
        block_sizes_p99=18,
        block_sizes_max=10_000,
        singleton_block_count=0,
        oversized_block_count=1,
    )
    assert bp.largest_block_pair_share > 0.9
    assert bp.health(n_rows=100_000) == HealthVerdict.RED


def test_uniform_coarse_blocking_is_not_skew():
    """8 equal blocks: each owns 12.5% of the work, and none is a straggler.

    Guards the fair-share clause. A flat 10% bar would grade this RED purely
    because there are few blocks, which is a coarseness complaint -- and
    coarseness is ``reduction_ratio``'s job, not skew's.
    """
    bp = BlockingProfile(
        keys_used=[["region"]],
        n_blocks=8,
        total_comparisons=39_600,  # 8 blocks of 100 rows -> 8 * 4,950
        reduction_ratio=0.876,
        block_sizes_p50=100,
        block_sizes_p95=100,
        block_sizes_p99=100,
        block_sizes_max=100,
        singleton_block_count=0,
        oversized_block_count=0,
    )
    assert bp.largest_block_pair_share == 0.125
    assert bp.health(n_rows=800) == HealthVerdict.GREEN


def test_few_blocks_with_one_straggler_is_caught_by_reduction_not_skew():
    """The fair-share clause is not a blanket exemption for small ``n_blocks``.

    It cannot be, because a dominating block is UNCONSTRUCTIBLE at small
    ``n_blocks`` without also cratering reduction: to own most of the pairs
    among 8 blocks it has to hold most of the rows, and then the layout is
    barely reducing anything. 8 blocks where one holds 700 of 800 rows keeps
    77% of all pairs, so ``reduction_ratio`` 0.233 grades it RED before the
    skew rule is consulted. This test pins that the exemption costs no
    coverage, which is the only reason it is safe to have.
    """
    bp = BlockingProfile(
        keys_used=[["region"]],
        n_blocks=8,
        # 244,650 pairs from the 700-row block + 7 blocks of ~14 (91 each).
        total_comparisons=245_287,
        reduction_ratio=0.233,
        block_sizes_p50=14,
        block_sizes_p95=14,
        block_sizes_p99=14,
        block_sizes_max=700,
        singleton_block_count=0,
        oversized_block_count=1,
    )
    assert bp.health(n_rows=800) == HealthVerdict.RED


def test_zero_total_comparisons_does_not_divide_by_zero():
    """A profile with blocks but no pairs must not raise. Every block is a
    singleton here, which is the YELLOW rule's territory, not the skew rule's."""
    bp = BlockingProfile(
        keys_used=[["id"]],
        n_blocks=1_000,
        total_comparisons=0,
        reduction_ratio=1.0,
        block_sizes_p50=1,
        block_sizes_p95=1,
        block_sizes_p99=1,
        block_sizes_max=1,
        singleton_block_count=1_000,
        oversized_block_count=0,
    )
    assert bp.largest_block_pair_share == 0.0
    assert bp.health(n_rows=1_000) == HealthVerdict.YELLOW


def test_reduction_ratio_rule_still_fires():
    """The other two RED rules are untouched by this change."""
    bp = BlockingProfile(
        keys_used=[["a"]],
        n_blocks=2,
        total_comparisons=4_900,
        reduction_ratio=0.01,
        block_sizes_p50=49,
        block_sizes_p95=49,
        block_sizes_p99=49,
        block_sizes_max=49,
    )
    assert bp.health(n_rows=100) == HealthVerdict.RED


def test_no_blocks_still_red():
    assert BlockingProfile().health(n_rows=100) == HealthVerdict.RED
