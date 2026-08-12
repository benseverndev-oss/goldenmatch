"""Analyzer side of #2488: token candidates are generated, scored, and committed.

The blocker itself is covered by `test_token_blocker.py`. This file covers the
part that closes the issue -- the analyzer being *able to propose* the shape.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.block_analyzer import (
    _FREE_TEXT_MIN_MEAN_TOKENS,
    _mean_token_count,
    estimate_recall,
    free_text_columns,
    generate_candidates,
    score_candidate,
)


def _titles_frame(n: int = 60) -> pl.DataFrame:
    """Free-text titles plus a short-identifier column, in one frame."""
    return pl.DataFrame({
        "title": [
            f"acme deluxe widget model {i} professional edition boxed retail"
            for i in range(n)
        ],
        "sku": [f"SKU{i:04d}" for i in range(n)],
    })


# ---- free-text detection is a property of the DATA, not the column name ----


def test_mean_token_count_measures_non_empty_values():
    df = pl.DataFrame({"c": ["one two three", "", None, "four five"]})
    assert _mean_token_count(df, "c") == 2.5  # (3 + 2) / 2, blanks excluded


def test_free_text_detection_picks_titles_and_rejects_short_ids():
    df = _titles_frame()
    assert free_text_columns(df, ["title", "sku"]) == ["title"]


def test_a_name_shaped_column_is_not_free_text():
    """The bar sits above the name range on purpose: prefix/soundex keys work
    well on names, so token candidates would only add cost there."""
    df = pl.DataFrame({"full_name": [f"John Smith {i}" for i in range(40)]})
    assert _mean_token_count(df, "full_name") < _FREE_TEXT_MIN_MEAN_TOKENS
    assert free_text_columns(df, ["full_name"]) == []


def test_missing_column_is_not_free_text():
    assert free_text_columns(pl.DataFrame({"a": ["x"]}), ["nope"]) == []


# ---- candidate generation ----


def test_token_candidates_are_off_by_default(monkeypatch):
    """Default OFF: auto-suggest must not propose token blocking until the
    integration bug that zeroed Amazon-Google F1 is resolved (#2488)."""
    monkeypatch.delenv("GOLDENMATCH_TOKEN_BLOCKING", raising=False)
    cands = generate_candidates(["title", "sku"], df=_titles_frame())
    assert all(c.get("kind") != "token" for c in cands)


def test_no_token_candidates_without_a_frame():
    """`df` is optional so the name-only callers keep working; they just get no
    token candidates rather than an error."""
    cands = generate_candidates(["title", "sku"])
    assert all(c.get("kind") != "token" for c in cands)


def test_token_candidates_are_generated_for_free_text_with_a_frame(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TOKEN_BLOCKING", "1")
    cands = generate_candidates(["title", "sku"], df=_titles_frame())
    tok = [c for c in cands if c.get("kind") == "token"]
    assert tok, "expected token candidates for the free-text column"
    assert {c["token"]["column"] for c in tok} == {"title"}
    assert len({c["token"]["max_df"] for c in tok}) > 1, "should offer a DF spread"


def test_token_candidates_are_not_compounded_with_exact_keys(monkeypatch):
    """ANDing a token block with a prefix key re-imposes the single-derived-value
    agreement that token blocking exists to avoid."""
    cands = generate_candidates(["title", "sku"], df=_titles_frame())
    for c in cands:
        if c.get("kind") == "token":
            assert len(c["key_fields"]) == 1
        else:
            assert "token" not in c


# ---- scoring ----


def _token_cand(column="title", max_df=100):
    return {
        "key_fields": [column], "transforms": [], "kind": "token",
        "token": {"column": column, "max_df": max_df},
        "description": f"tokens({column}, df<={max_df})",
    }


def test_score_candidate_handles_a_token_candidate():
    m = score_candidate(_titles_frame(), _token_cand())
    assert m["group_count"] > 0
    assert m["total_comparisons"] > 0
    assert 0.0 < m["coverage"] <= 1.0
    assert 0.0 < m["score"] <= 1.0


def test_token_score_stays_within_zero_and_one_despite_multi_membership():
    """A record joins many blocks, so `group_count` can exceed the row count --
    the exact-key selectivity term (`group_count / n_total`) would leave [0, 1]
    and make the two candidate shapes non-comparable. Coverage replaces it."""
    df = _titles_frame(n=30)
    m = score_candidate(df, _token_cand(max_df=200))
    assert m["group_count"] > 0
    assert 0.0 <= m["score"] <= 1.0


def test_token_candidate_on_a_missing_column_scores_zero():
    m = score_candidate(_titles_frame(), _token_cand(column="nope"))
    assert m["score"] == 0.0 and m["group_count"] == 0


def test_df_cap_trades_recall_for_cost():
    """Tighter caps must not cost MORE -- pruning strictly removes blocks."""
    df = _titles_frame(n=80)
    tight = score_candidate(df, _token_cand(max_df=5))
    loose = score_candidate(df, _token_cand(max_df=200))
    assert tight["total_comparisons"] <= loose["total_comparisons"]


# ---- recall estimation ----


def test_estimate_recall_uses_shared_token_not_key_equality():
    """The multi-key path: these two titles share discriminative tokens but no
    common prefix, so an exact key scores 0 recall and tokens score 1."""
    df = pl.DataFrame({
        "title": [
            "adobe photoshop elements nine premium boxed edition",
            "photoshop elements nine adobe systems premium boxed",
        ] * 6,
    })
    tok = estimate_recall(df, _token_cand(max_df=100), ["title"], sample_size=12)
    prefix = estimate_recall(
        df,
        {"key_fields": ["title"], "transforms": ["lowercase", "substring:0:5"],
         "description": "title[:5]"},
        ["title"], sample_size=12,
    )
    assert tok > prefix


# ---- the pipeline commits it as a STRATEGY, not as a key ----


def test_auto_suggest_commits_a_token_plan_as_strategy_token(monkeypatch):
    """`BlockingConfig` rejects `keys` alongside `token`, so committing a token
    suggestion means switching the strategy -- not appending a key."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core import pipeline as P

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="weighted", threshold=0.8, fields=[
            MatchkeyField(field="title", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(strategy="static", auto_suggest=True),
    )

    top = P.analyze_blocking.__globals__["BlockingSuggestion"](
        keys=[_token_cand(max_df=50)],
        group_count=10, max_group_size=5, mean_group_size=2.0,
        total_comparisons=100, estimated_recall=0.9, score=0.5,
        description="tokens(title, df<=50)",
    )
    monkeypatch.setattr(P, "analyze_blocking", lambda *a, **k: [top])

    P._run_auto_suggest(_titles_frame(), cfg)

    assert cfg.blocking.strategy == "token"
    assert cfg.blocking.token is not None
    assert cfg.blocking.token.column == "title"
    assert cfg.blocking.token.max_df == 50
    assert not cfg.blocking.keys, "a token plan must not also populate keys"


def test_auto_suggest_still_commits_exact_keys_unchanged(monkeypatch):
    """The token branch must not capture the ordinary path."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core import pipeline as P

    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="weighted", threshold=0.8, fields=[
            MatchkeyField(field="title", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(strategy="static", auto_suggest=True),
    )
    top = P.analyze_blocking.__globals__["BlockingSuggestion"](
        keys=[{"key_fields": ["title"], "transforms": ["lowercase", "substring:0:5"],
               "description": "title[:5]"}],
        group_count=10, max_group_size=5, mean_group_size=2.0,
        total_comparisons=100, estimated_recall=0.5, score=0.5,
        description="title[:5]",
    )
    monkeypatch.setattr(P, "analyze_blocking", lambda *a, **k: [top])

    P._run_auto_suggest(_titles_frame(), cfg)

    assert cfg.blocking.strategy == "static"
    assert cfg.blocking.token is None
    assert [k.fields for k in cfg.blocking.keys] == [["title"]]


def test_person_shaped_data_gets_no_token_candidates(monkeypatch):
    """Regression guard on the blast radius. Token blocking must not reach the
    person benchmarks (Febrl3/NCVR sit at ~0.99 F1 on prefix/soundex keys).
    Street addresses are the boundary case -- the longest person field -- and
    measure ~2.95 mean tokens on the NCVR synthetic corpus, well under the bar.
    """
    df = pl.DataFrame({
        "first_name": ["John", "Jane", "Bob"] * 8,
        "last_name": ["Smith", "Doe", "Jones"] * 8,
        "res_street_address": ["123 Main St", "45 Oak Avenue", "9 Elm Rd"] * 8,
        "zip_code": ["27601", "27603", "27605"] * 8,
    })
    cols = ["first_name", "last_name", "res_street_address", "zip_code"]
    assert free_text_columns(df, cols) == []
    monkeypatch.setenv("GOLDENMATCH_TOKEN_BLOCKING", "1")  # on, and still none
    assert all(c.get("kind") != "token" for c in generate_candidates(cols, df=df))


def test_score_discounts_a_plan_that_proposes_too_many_pairs():
    """The regression that made Amazon-Google F1 0.0 (#2488).

    `max_group_size` is the exact-key cost proxy, and it works there because one
    block per record means the biggest block dominates the pair count. Under
    multi-key blocking that breaks: a plan can have tiny blocks and an enormous
    total. Scored on max_group_size alone such a plan looks cheap, the pipeline
    then blows the auto-config time budget scoring its pairs, and the run falls
    back to degenerate blocking -- worse than the key it replaced.

    Both frames below give blocks of exactly 10, full coverage and zero size
    variance, so every other term in the score is identical. The only
    difference is fan-out: 1 token per record (10 blocks, 450 pairs) versus 5
    (50 blocks, 2250 pairs). Without a total-pair term the two score the same.
    """
    def frame(tokens_per_record: int) -> pl.DataFrame:
        return pl.DataFrame({"title": [
            " ".join(f"tok{t}grp{i // 10}" for t in range(tokens_per_record))
            for i in range(100)
        ]})

    cheap = score_candidate(frame(1), _token_cand(max_df=200))
    costly = score_candidate(frame(5), _token_cand(max_df=200))

    assert cheap["max_group_size"] == costly["max_group_size"] == 10
    assert cheap["coverage"] == costly["coverage"] == 1.0
    assert costly["total_comparisons"] == 5 * cheap["total_comparisons"]
    assert costly["score"] < cheap["score"], (
        "more total pairs at identical block size must score lower "
        f"(cheap={cheap}, costly={costly})"
    )
