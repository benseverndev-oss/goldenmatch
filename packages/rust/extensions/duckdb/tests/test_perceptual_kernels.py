"""Tests for the perceptual pHash UDFs (``goldenmatch_perceptual_phash`` /
``goldenmatch_perceptual_hamming``).

The UDFs run the native-gated ``goldenmatch.core.perceptual`` kernel, so their
output must match that reference exactly (the SQL surface is not a
reimplementation) and — via the shared kernel — the Rust / Postgres surfaces.
"""
from __future__ import annotations

import duckdb
import pytest
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


def _ramp(n=8):
    """Row-major flat 8x8 ramp grid [0, 1, ..., 63]."""
    return [float(i) for i in range(n * n)]


def _signed(h: int) -> int:
    return h - (1 << 64) if h >= (1 << 63) else h


def _phash(con, flat, ncols):
    return con.execute(
        "SELECT goldenmatch_perceptual_phash(?, ?)", [flat, ncols]
    ).fetchone()[0]


class TestPerceptualPhash:
    def test_matches_python_reference(self, con):
        from goldenmatch.core.perceptual import phash_image

        flat = _ramp(8)
        rows = [[flat[r * 8 + c] for c in range(8)] for r in range(8)]
        expected = _signed(phash_image(rows))
        assert _phash(con, flat, 8) == expected

    def test_pinned_ramp_value(self, con):
        # Pinned vector shared with the Rust golden.rs / pgrx smoke.
        assert _phash(con, _ramp(8), 8) == -7026231021782789055

    def test_rejects_ragged_grid(self, con):
        with pytest.raises(Exception):
            _phash(con, [1.0, 2.0, 3.0], 2)  # 3 not a multiple of 2


class TestPerceptualHamming:
    def _h(self, con, a, b):
        return con.execute(
            "SELECT goldenmatch_perceptual_hamming(?, ?)", [a, b]
        ).fetchone()[0]

    def test_self_distance_zero(self, con):
        assert self._h(con, 42, 42) == 0
        assert self._h(con, -7026231021782789055, -7026231021782789055) == 0

    def test_ramp_vs_inverted(self, con):
        # ramp and inverted-ramp pHashes differ in 48 of 64 bits (pinned).
        assert self._h(con, -7026231021782789055, 7004135197453085675) == 48

    def test_matches_python_hamming(self, con):
        from goldenmatch.core.perceptual import hamming, phash_image

        g1 = [[float(r * 8 + c) for c in range(8)] for r in range(8)]
        g2 = [[float((r * 8 + c) ^ 1) for c in range(8)] for r in range(8)]
        h1, h2 = phash_image(g1), phash_image(g2)
        # Feed the signed BIGINT forms the phash UDF would store.
        got = self._h(con, _signed(h1), _signed(h2))
        assert got == hamming(h1, h2)
