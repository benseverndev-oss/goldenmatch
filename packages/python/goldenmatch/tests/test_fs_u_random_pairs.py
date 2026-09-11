"""``GOLDENMATCH_FS_U_PAIRS``: the random-pair budget ``train_em`` estimates ``u`` from.

Unset must keep the historical ``min(n_sample_pairs, 5000)`` so the default path is
byte-identical; a set value replaces it. Measured locally on dblp_acm, zero-config FS
F1 0.3758 at 5,000 pairs -> 0.8058 at 50,000 -> 0.8242 at 200,000: exact title/author
agreement never occurs in 5,000 random pairs, so its u hit the smoothing floor and the
agreement weight exploded past the exact level's.
"""

from __future__ import annotations

from goldenmatch.core.probabilistic import _fs_u_random_pairs


def test_default_is_the_historical_cap(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_U_PAIRS", raising=False)
    assert _fs_u_random_pairs(10_000) == 5000
    assert _fs_u_random_pairs(1_200) == 1_200


def test_override_replaces_the_cap(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "200000")
    assert _fs_u_random_pairs(10_000) == 200_000


def test_override_has_a_floor(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "3")
    assert _fs_u_random_pairs(10_000) == 10


def test_zero_is_the_default_not_a_tiny_sample(monkeypatch):
    """KNOWN-POSITIVE: fs-lever-gate runs its OFF arm with the lever set to "0". Read as
    max(10, 0) = 10 random pairs, that arm scored household_hardneg F1 0.2579 and
    dblp_acm 0.8323 instead of the real default's 1.0000 and 0.3758 (run 34546541515)."""
    for off in ("0", "-5"):
        monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", off)
        assert _fs_u_random_pairs(10_000) == 5000


def test_non_integer_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "lots")
    assert _fs_u_random_pairs(10_000) == 5000
