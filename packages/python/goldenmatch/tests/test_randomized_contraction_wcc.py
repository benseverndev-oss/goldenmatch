"""Tests for randomized-contraction WCC (#844 Spec 1).

Pure-Polars correctness tests run without Ray; the Ray-orchestration and
routing tests are gated with a function-level importorskip.
"""
import polars as pl
import pytest


def test_rc_prime_is_mersenne():
    from goldenmatch.distributed.clustering import _RC_PRIME
    assert _RC_PRIME == 2**31 - 1
