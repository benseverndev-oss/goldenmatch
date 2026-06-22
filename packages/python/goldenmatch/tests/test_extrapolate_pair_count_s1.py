"""S1 (spec 2026-06-22-autoconfig-smarter-faster-s1-s3): the corrected
BlockingProfile.extrapolate_to kernel.

Pairs scale by ratio**2 (integer-exact), capped at the all-pairs maximum;
n_blocks uses a Chao1 richness estimate when F1/F2 were measured, else a linear
fallback. These pin the exact numbers the Rust core kernel must also produce.
"""
from __future__ import annotations

from goldenmatch.core.complexity_profile import BlockingProfile


def test_extrapolate_pairs_quadratic():
    # ratio=100; pairs scale by ratio**2; well under the all-pairs cap.
    bp = BlockingProfile(n_blocks=10, total_comparisons=100, chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(1_000, 100_000)
    assert out.total_comparisons == 100 * 100_000 * 100_000 // (1_000 * 1_000)  # 1_000_000
    assert out.n_blocks == 10 * 100_000 // 1_000  # linear fallback: 1000


def test_extrapolate_pairs_cap_inert_for_legit_input():
    # All-pairs cap does NOT trigger for legitimate measured input
    # (total_comparisons <= C(n_sample,2)). tc=10, ns=10, nf=20:
    # raw = 10*20*20//(10*10) = 40; cap = 20*19//2 = 190 -> min = 40.
    bp = BlockingProfile(n_blocks=2, total_comparisons=10, chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(10, 20)
    assert out.total_comparisons == 40


def test_extrapolate_pairs_cap_clamps_pathological_input():
    # Defensive rail: tc=50 EXCEEDS the sample all-pairs max C(10,2)=45;
    # raw = 50*400//100 = 200 > cap 190 -> clamp to 190.
    bp = BlockingProfile(n_blocks=2, total_comparisons=50, chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(10, 20)
    assert out.total_comparisons == 20 * 19 // 2  # 190


def test_extrapolate_nblocks_chao1():
    # F1/F2 present -> Chao1 richness: observed=(n_blocks+F1) + F1**2//(2*(F2+1)).
    bp = BlockingProfile(n_blocks=50, total_comparisons=100, chao1_f1=10, chao1_f2=5)
    out = bp.extrapolate_to(1_000, 100_000)
    assert out.n_blocks == (50 + 10) + 10 * 10 // (2 * (5 + 1))  # 60 + 8 = 68


def test_extrapolate_nblocks_chao1_capped_at_full_rows():
    # Many singletons, few doubletons -> Chao1 can exceed n_full; cap at n_full.
    bp = BlockingProfile(n_blocks=10, total_comparisons=10, chao1_f1=1_000, chao1_f2=0)
    out = bp.extrapolate_to(2_000, 50)  # tiny full N forces the n_blocks cap
    assert out.n_blocks == 50  # min(huge_chao1, n_full)


def test_extrapolate_noop_on_bad_args():
    bp = BlockingProfile(n_blocks=5, total_comparisons=10)
    assert bp.extrapolate_to(0, 100) is bp
    assert bp.extrapolate_to(100, 0) is bp
