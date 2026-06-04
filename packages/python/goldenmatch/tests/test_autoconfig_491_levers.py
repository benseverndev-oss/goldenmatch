"""#491 lever-coverage: real qgram char-n-gram similarity scorer.

Task 0: qgram was only a lossy ``qgram:N`` transform; a genuine
character-n-gram Jaccard *scorer* is needed for short-code routing.
"""

from __future__ import annotations

from goldenmatch.core.scorer import score_field


def test_qgram_scorer_similarity():
    assert score_field("ABC123", "ABC123", "qgram") == 1.0  # identical
    disjoint = score_field("ABC123", "XYZ789", "qgram")
    assert disjoint is not None and disjoint < 0.2  # disjoint
    s = score_field("ABC123", "ABC132", "qgram")
    assert s is not None and 0.3 < s < 1.0  # transposition-ish


def test_qgram_scorer_empty_handling():
    # Both empty -> identical -> 1.0
    assert score_field("", "", "qgram") == 1.0
    # One empty, one not -> no shared grams -> 0.0
    assert score_field("", "ABC123", "qgram") == 0.0


def test_qgram_scorer_matrix_matches_single():
    from goldenmatch.core.scorer import _fuzzy_score_matrix

    vals = ["ABC123", "ABC132", "XYZ789"]
    m = _fuzzy_score_matrix(vals, "qgram")
    n = len(vals)
    assert m.shape == (n, n)
    # Diagonal is self-similarity == 1.0
    for i in range(n):
        assert m[i, i] == 1.0
    # Off-diagonal matches the single-pair scorer
    for i in range(n):
        for j in range(n):
            if i != j:
                single = score_field(vals[i], vals[j], "qgram")
                assert single is not None
                assert abs(m[i, j] - single) < 1e-9


# ── Task 1: short-code columns route to qgram in build_matchkeys ────────────


def _df_with(cols: list[str]):
    import polars as pl

    return pl.DataFrame({c: ["x", "y", "z"] for c in cols})


def test_short_code_column_gets_qgram():
    from goldenmatch.core.autoconfig import ColumnProfile, build_matchkeys

    profiles = [
        ColumnProfile("sku", "Utf8", "string", 0.9,
                      sample_values=["A1B2C3", "X9Y8Z7", "Q2W3E4"],
                      null_rate=0.0, cardinality_ratio=0.7, avg_len=6.0),
        ColumnProfile("first_name", "Utf8", "name", 0.9,
                      sample_values=["james", "mary", "john"],
                      null_rate=0.0, cardinality_ratio=0.02, avg_len=5.0),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["sku", "first_name"]))
    scorers = {f.field: f.scorer for mk in mks for f in mk.fields}
    assert scorers.get("sku") == "qgram"
    assert scorers.get("first_name") != "qgram"


# ── Task 2: optimizer scorer-family includes qgram ───────────────────────────


def test_optimizer_scorer_family_includes_qgram():
    from goldenmatch.core.config_optimizer import CoordinateDescentProposer

    assert "qgram" in CoordinateDescentProposer()._scorers


# ── Task 3: optimizer proposes weighted->probabilistic matchkey-type swaps ────


def test_optimizer_proposes_probabilistic_candidate():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.config_optimizer import (
        CoordinateDescentProposer,
        SearchState,
    )

    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=0.8, rerank=False,
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0, transforms=[])],
        )],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        ),
    )
    state = SearchState(base_config=config, objective="confidence")

    proposer = CoordinateDescentProposer()
    types: set[str] = set()
    # Drain every family (propose returns one family per call).
    while True:
        cands = proposer.propose(state)
        if not cands:
            break
        for _label, cfg in cands:
            for mk in cfg.get_matchkeys():
                if mk.type is not None:
                    types.add(mk.type)

    assert "probabilistic" in types
