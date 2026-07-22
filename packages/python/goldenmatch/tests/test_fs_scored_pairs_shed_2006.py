"""Issue #2006 (FS #1811 follow-up) — complete the peak-RSS win: on the B2c
columnar-cluster path (`GOLDENMATCH_FS_COLUMNAR_CLUSTER`) the post-cluster
`scored_pairs` list is the LAST driver-resident `list[tuple]` accumulator #1811
left. At 14M on tight-blocking data it runs to O(hundreds of millions of tuples)
-> still OOMs.

The fix: dedup the pair stream COLUMNAR (`dedup_pairs_max_score_arrow`, no
`list[tuple]` intermediate) and SHED the driver list above
`GOLDENMATCH_FS_SCORED_PAIRS_MAX` (default 50M) -- clusters/golden are already
built off `_columnar_pairs_df`, so only the steward-facing raw pair list drops,
and never silently (`DedupeResult.scored_pairs_shed`).

These pin: (1) below the cap, B2c materializes `scored_pairs` (non-empty,
marker False) and clusters are built; (2) above the cap (forced to 1), the list
is shed (`scored_pairs == []`, marker True) yet the SAME multi-member clusters
are still produced; (3) the cap is B2c-scoped -- with the flag OFF the shed
never fires regardless of the cap. Same clear-margin fixture + determinism
posture as the #1811 (B2c) test.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "first_name": ["john", "john", "jon", "mary", "mary", "zoe"],
        "last_name": ["smith", "smith", "smith", "jones", "jones", "xu"],
        "email": ["j@x.com", "j@x.com", "j@x.com", "m@x.com", "m@x.com", "z@x.com"],
    })


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
        backend="bucket",
    )


def _members(res) -> frozenset:
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in (res.clusters or {}).values()
        if len(c.get("members", [])) > 1
    )


def _run(monkeypatch, *, flag: bool, cap: str | None):
    import goldenmatch as gm

    monkeypatch.setenv("GOLDENMATCH_FS_COLUMNAR_CLUSTER", "1" if flag else "0")
    monkeypatch.setenv("GOLDENMATCH_FS_WORKERS", "1")
    if cap is not None:
        monkeypatch.setenv("GOLDENMATCH_FS_SCORED_PAIRS_MAX", cap)
    return gm.dedupe_df(_df(), config=_cfg(), confidence_required=False)


_ANCHOR = {frozenset({0, 1, 2}), frozenset({3, 4})}


def test_b2c_below_cap_materializes_scored_pairs(monkeypatch):
    """B2c ON, default (high) cap: scored_pairs is materialized, NOT shed, and
    the clusters are built."""
    res = _run(monkeypatch, flag=True, cap=None)
    assert res.scored_pairs_shed is False
    assert len(res.scored_pairs) > 0, "below the cap the raw pair list is kept"
    assert _members(res) == _ANCHOR, _members(res)


def test_b2c_above_cap_sheds_but_keeps_clusters(monkeypatch):
    """B2c ON, cap forced to 1: scored_pairs is SHED (empty + marker) yet the
    SAME multi-member clusters still come out (built off the columnar frame)."""
    res = _run(monkeypatch, flag=True, cap="1")
    assert res.scored_pairs_shed is True, "the cap must trip the shed marker"
    assert res.scored_pairs == [], "above the cap the driver list is shed"
    assert _members(res) == _ANCHOR, _members(res)  # clusters unaffected


def test_cap_is_b2c_scoped(monkeypatch):
    """Flag OFF (list path): the cap never fires -- scored_pairs is materialized
    and the marker stays False even at cap=1."""
    res = _run(monkeypatch, flag=False, cap="1")
    assert res.scored_pairs_shed is False
    assert len(res.scored_pairs) > 0
    assert _members(res) == _ANCHOR, _members(res)


def test_cap_zero_disables_shed(monkeypatch):
    """`GOLDENMATCH_FS_SCORED_PAIRS_MAX=0` disables the cap -> always materialize."""
    res = _run(monkeypatch, flag=True, cap="0")
    assert res.scored_pairs_shed is False
    assert len(res.scored_pairs) > 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
