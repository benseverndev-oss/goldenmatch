"""`estimate_recall`'s denominator must be the pairs blocking is accountable for
losing -- the ones the configured matchkey would emit (#2513).

Blocking's job is not to find true duplicates; that is the scorer's job. It is
to avoid losing pairs the scorer would have matched. The analyzer instead scored
candidates against "pairs whose Jaro-Winkler similarity on the highest-cardinality
matchkey column is >= 0.7", which on Amazon-Google is 98.5% non-duplicates (2,355
sample pairs, 35 true). Candidates were therefore judged largely on how many
NON-matches they co-blocked, and the more discriminative the candidate the worse
it scored:

    candidate                estimated   true pair recall
    tokens(title, df<=10)        26.0%              65.9%
    tokens(title, df<=25)        46.1%              87.6%
    tokens(title, df<=50)        73.6%              95.3%
    tokens(title, df<=100)        0.0% (*)          98.2%

    (*) outside the top 10 by score, so never measured -- see
        test_block_analyzer_recall_2488.py for that half.

Scoring the sample with the real matchkey brings every one of those within ~3pp
of truth, and costs less: 537 target pairs in 1.0s against 2,355 in 19.6s, and
built once instead of once per candidate.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.block_analyzer import (
    _build_recall_target,
    _retention,
    _target_pairs_from_matchkey,
    analyze_blocking,
    estimate_recall,
)


@pytest.fixture
def df() -> pl.DataFrame:
    """Three tight duplicate pairs plus four filler rows. The duplicates differ
    enough that an exact key on the full string splits them; the filler rows
    share no tokens with each other or with the pairs, so the scorer emits
    exactly the three planted pairs and nothing else."""
    return pl.DataFrame({
        "name": [
            "acme widget pro", "acme widget pro x",       # pair 0-1
            "globex turbo drive", "globex turbo drives",  # pair 2-3
            "initech stapler v2", "initech stapler v3",   # pair 4-5
            "zebra", "quartz mining", "helicopter fuel", "opera house",
        ],
    })


def _mk(threshold: float = 0.85) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="mk", type="weighted", threshold=threshold,
        fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0,
                              transforms=["lowercase", "strip"])],
    )


# ---- the target population ----


def test_matchkey_target_is_the_pairs_the_scorer_emits(df: pl.DataFrame) -> None:
    """Definitional: the denominator is what the configured matchkey matches,
    not a stand-in for it."""
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    pairs = _target_pairs_from_matchkey(frame, _mk())
    assert pairs, "the matchkey should emit the near-identical pairs"
    # Every emitted pair is one of the three planted duplicates.
    assert pairs <= {(0, 1), (2, 3), (4, 5)}


def test_target_pairs_are_canonically_ordered(df: pl.DataFrame) -> None:
    from goldenmatch.core.frame import to_frame

    pairs = _target_pairs_from_matchkey(to_frame(df), _mk())
    assert all(i < j for i, j in pairs)


def test_a_stricter_threshold_yields_no_more_pairs(df: pl.DataFrame) -> None:
    """Sanity on the wiring: the target really is threshold-driven, so raising
    the matchkey's bar cannot admit pairs."""
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    loose = _target_pairs_from_matchkey(frame, _mk(threshold=0.5))
    strict = _target_pairs_from_matchkey(frame, _mk(threshold=0.99))
    assert strict <= loose


def test_matchkey_is_preferred_over_the_similarity_proxy(df: pl.DataFrame) -> None:
    """When a matchkey is available the target must come from it, not from the
    fixed JW >= 0.7 proxy.

    Shown with a strict matchkey, which admits fewer pairs than the proxy does.
    The size gap on real free text is the actual defect and is far larger --
    537 matchkey pairs against 2,355 proxy pairs on an Amazon-Google sample --
    but a ten-row fixture of deliberately dissimilar strings cannot exhibit
    that, so this asserts the wiring rather than the magnitude.
    """
    _, strict = _build_recall_target(df, ["name"], 1000, _mk(threshold=0.99))
    _, proxy = _build_recall_target(df, ["name"], 1000, None)
    assert len(strict) < len(proxy), (
        "the target tracked the proxy instead of the supplied matchkey"
    )


def test_falls_back_to_the_proxy_when_scoring_raises(
    df: pl.DataFrame, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail open: a broken matchkey must degrade to the old proxy, not to an
    empty target (which would read as "nothing to lose" -> recall 1.0 for
    every candidate)."""
    def _boom(frame: object, matchkey: object) -> set[tuple[int, int]]:
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr(
        "goldenmatch.core.block_analyzer._target_pairs_from_matchkey", _boom
    )
    with caplog.at_level("WARNING"):
        _, pairs = _build_recall_target(df, ["name"], 1000, _mk())
    assert pairs, "must fall back to the similarity proxy, not an empty target"
    assert "falling back" in caplog.text


def test_no_matchkey_columns_and_no_matchkey_gives_an_empty_target(df: pl.DataFrame) -> None:
    _, pairs = _build_recall_target(df, ["nonexistent"], 1000, None)
    assert pairs == set()


# ---- retention against that population ----


def test_a_key_that_co_blocks_the_target_pairs_scores_high(df: pl.DataFrame) -> None:
    """`name[:6]` keeps all three planted pairs together."""
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    target = _target_pairs_from_matchkey(frame, _mk())
    cand = {"key_fields": ["name"], "transforms": ["lowercase", "substring:0:6"],
            "description": "name[:6]"}
    assert _retention(frame, cand, target) == 1.0


def test_a_key_that_splits_them_scores_low(df: pl.DataFrame) -> None:
    """An exact key on the full string separates every planted pair, because
    each differs by a character -- which is exactly why they needed fuzzy
    scoring in the first place."""
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    target = _target_pairs_from_matchkey(frame, _mk())
    cand = {"key_fields": ["name"], "transforms": ["lowercase"],
            "description": "name"}
    assert _retention(frame, cand, target) == 0.0


def test_empty_target_is_full_retention_not_zero(df: pl.DataFrame) -> None:
    """Nothing to lose is not the same as losing everything."""
    from goldenmatch.core.frame import to_frame

    cand = {"key_fields": ["name"], "transforms": ["lowercase"], "description": "name"}
    assert _retention(to_frame(df), cand, set()) == 1.0


# ---- the public entry point keeps working ----


def test_estimate_recall_still_callable_without_a_matchkey(df: pl.DataFrame) -> None:
    """CLI / MCP / A2A callers pass column names only."""
    cand = {"key_fields": ["name"], "transforms": ["lowercase", "substring:0:6"],
            "description": "name[:6]"}
    r = estimate_recall(df, cand, ["name"])
    assert 0.0 <= r <= 1.0


def test_estimate_recall_accepts_a_matchkey(df: pl.DataFrame) -> None:
    cand = {"key_fields": ["name"], "transforms": ["lowercase", "substring:0:6"],
            "description": "name[:6]"}
    assert estimate_recall(df, cand, ["name"], matchkey=_mk()) == 1.0


def test_estimate_recall_on_a_degenerate_frame(df: pl.DataFrame) -> None:
    cand = {"key_fields": ["name"], "transforms": ["lowercase"], "description": "name"}
    assert estimate_recall(pl.DataFrame({"name": ["only"]}), cand, ["name"]) == 0.0


# ---- the analyzer threads it through ----


def test_analyze_blocking_accepts_and_uses_a_matchkey(df: pl.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    real = _build_recall_target

    def _spy(frame: pl.DataFrame, cols: list[str], sample_size: int,
             matchkey: object = None) -> tuple[object, set[tuple[int, int]]]:
        seen["matchkey"] = matchkey
        return real(frame, cols, sample_size, matchkey)

    monkeypatch.setattr("goldenmatch.core.block_analyzer._build_recall_target", _spy)
    mk = _mk()
    analyze_blocking(df, ["name"], matchkey=mk)
    assert seen["matchkey"] is mk


def test_the_target_is_built_once_not_per_candidate(df: pl.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hoist is load-bearing, not cosmetic: the sample is seeded, so every
    candidate saw an identical population that was rebuilt from scratch each
    time -- ~19.6s of O(n^2) work repeated ten times on Amazon-Google, which
    was essentially the analyzer's whole 196s runtime."""
    calls = []
    real = _build_recall_target

    def _count(frame: pl.DataFrame, cols: list[str], sample_size: int,
               matchkey: object = None) -> tuple[object, set[tuple[int, int]]]:
        calls.append(1)
        return real(frame, cols, sample_size, matchkey)

    monkeypatch.setattr("goldenmatch.core.block_analyzer._build_recall_target", _count)
    sugs = analyze_blocking(df, ["name"], matchkey=_mk())
    assert len(sugs) > 1, "need several candidates for the assertion to bite"
    assert len(calls) == 1
