"""The random-pair budget ``train_em`` estimates ``u`` from.

``min(5 * n_sample_pairs, 50_000)`` -- 50,000 at the default ``n_sample_pairs=10000``,
where it used to be ``min(n_sample_pairs, 5000)``. Exact title/author agreement never
occurs in 5,000 random bibliographic pairs, so its u hit the smoothing floor and the
agreement weight exploded past the exact level's. Best F1 per dataset over the lever
gate's cutoffs: dblp_acm 0.8613 -> 0.9077 and dblp_scholar 0.4213 -> 0.5050, no dataset
losing more than historical_50k's 0.0047. ``GOLDENMATCH_FS_U_PAIRS`` sets it outright.
"""

from __future__ import annotations

from goldenmatch.core.probabilistic import _FS_U_PAIRS_DEFAULT, _fs_u_random_pairs


def test_default_rule(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_U_PAIRS", raising=False)
    assert _FS_U_PAIRS_DEFAULT == 50_000
    assert _fs_u_random_pairs(10_000) == 50_000  # the pipeline's default EM sample
    assert _fs_u_random_pairs(2_000_000) == 50_000  # capped
    assert _fs_u_random_pairs(20) == 100  # a small caller budget stays small (#1803)


def test_override_replaces_the_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "5000")
    assert _fs_u_random_pairs(10_000) == 5000
    assert _fs_u_random_pairs(20) == 5000


def test_override_has_a_floor(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "3")
    assert _fs_u_random_pairs(10_000) == 10


def test_zero_is_the_default_not_a_tiny_sample(monkeypatch):
    """KNOWN-POSITIVE: fs-lever-gate runs its OFF arm with the lever set to "0". Read as
    max(10, 0) = 10 random pairs, that arm scored household_hardneg F1 0.2579 and
    dblp_acm 0.8323 instead of the real default's 1.0000 and 0.3758 (run 34546541515)."""
    for off in ("0", "-5"):
        monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", off)
        assert _fs_u_random_pairs(10_000) == 50_000


def test_non_integer_falls_back_to_the_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_U_PAIRS", "lots")
    assert _fs_u_random_pairs(10_000) == 50_000
