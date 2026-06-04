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


# ── Task 4: conservative controller refit rule -> probabilistic matchkey ──────


def _491_cfg(*, exact_anchor: bool, n_fuzzy_fields: int):
    """Build a config with one weighted matchkey of ``n_fuzzy_fields`` graded
    fuzzy fields (plus optionally a separate exact matchkey)."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    fuzzy_scorers = ["jaro_winkler", "levenshtein", "token_sort", "qgram"]
    fuzzy_fields = [
        MatchkeyField(
            field=f"f{i}", scorer=fuzzy_scorers[i % len(fuzzy_scorers)],
            weight=1.0, transforms=["lowercase"],
        )
        for i in range(n_fuzzy_fields)
    ]
    matchkeys = [
        MatchkeyConfig(
            name="weighted_mk", type="weighted", threshold=0.8,
            fields=fuzzy_fields,
        )
    ]
    if exact_anchor:
        matchkeys.append(
            MatchkeyConfig(
                name="exact_mk", type="exact",
                fields=[MatchkeyField(field="email", transforms=["lowercase"])],
            )
        )
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"])],
        ),
    )


def _491_profile(*, recall_limited: bool):
    """ComplexityProfile whose scoring is recall-limited (low mass above
    threshold + poor separation) or healthy."""
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ClusterProfile,
        ComplexityProfile,
        DataProfile,
        FieldStats,
        MatchkeyProfile,
        ScoringProfile,
    )

    if recall_limited:
        scoring = ScoringProfile(
            n_pairs_scored=800, candidates_compared=900,
            mass_above_threshold=0.04,   # very few pairs reach threshold
            mass_in_borderline=0.05,
            dip_statistic=0.008,         # poor separation (near-unimodal)
        )
    else:
        scoring = ScoringProfile(
            n_pairs_scored=800, candidates_compared=900,
            mass_above_threshold=0.6,    # healthy: clear above-threshold mass
            mass_in_borderline=0.05,
            dip_statistic=0.08,          # clear bimodal separation
        )
    return ComplexityProfile(
        data=DataProfile(
            n_rows=2000, n_cols=5,
            column_types={"f0": "name", "f1": "text", "f2": "geo",
                          "f3": "text", "email": "id-like"},
        ),
        blocking=BlockingProfile(
            keys_used=[["last_name"]], n_blocks=400, total_comparisons=900,
            reduction_ratio=0.9, block_sizes_p99=10,
        ),
        scoring=scoring,
        cluster=ClusterProfile(transitivity_rate=0.9),
        matchkey=MatchkeyProfile(per_field={"f0": FieldStats(0.5, 0.0, 8)}),
    )


def test_rule_selects_probabilistic_on_target_shape():
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import (
        rule_select_probabilistic_matchkey,
    )

    cfg = _491_cfg(exact_anchor=False, n_fuzzy_fields=3)
    profile = _491_profile(recall_limited=True)
    out = rule_select_probabilistic_matchkey(profile, cfg, RunHistory())
    assert out is not None
    new_cfg, decision = out
    swapped = next(mk for mk in new_cfg.get_matchkeys() if mk.name == "weighted_mk")
    assert swapped.type == "probabilistic"
    assert decision.rule_name == "select_probabilistic_matchkey"


def test_rule_skips_when_exact_anchor_present():
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import (
        rule_select_probabilistic_matchkey,
    )

    cfg = _491_cfg(exact_anchor=True, n_fuzzy_fields=3)
    profile = _491_profile(recall_limited=True)
    assert rule_select_probabilistic_matchkey(profile, cfg, RunHistory()) is None


def test_rule_skips_small_or_healthy():
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import (
        rule_select_probabilistic_matchkey,
    )

    # Too few graded fuzzy fields (2 < 3) — should not fire even when recall-limited.
    small_cfg = _491_cfg(exact_anchor=False, n_fuzzy_fields=2)
    recall_limited = _491_profile(recall_limited=True)
    assert rule_select_probabilistic_matchkey(recall_limited, small_cfg, RunHistory()) is None

    # Target field count but healthy scoring — should not fire.
    target_cfg = _491_cfg(exact_anchor=False, n_fuzzy_fields=3)
    healthy = _491_profile(recall_limited=False)
    assert rule_select_probabilistic_matchkey(healthy, target_cfg, RunHistory()) is None


# ── Task 5: ANN blocking auto-selected with embeddings at scale (#491) ────────


def _profiles_with_embeddings():
    """Profiles that carry an embedding signal: a ``description`` column
    (becomes a ``record_embedding`` scorer / ANN-eligible vector column),
    plus an ordinary name column. Crucially there is NO bounded exact
    blocking column here, so ANN is genuinely the best blocking option and
    the fallback gate (#491) lets it fire."""
    from goldenmatch.core.autoconfig import ColumnProfile

    return [
        ColumnProfile(
            "bio", "Utf8", "description", 0.9,
            sample_values=[
                "senior cardiologist at mercy hospital",
                "pediatric nurse practitioner downtown clinic",
                "radiology technician regional medical center",
            ],
            null_rate=0.0, cardinality_ratio=0.95, avg_len=42.0,
        ),
        ColumnProfile(
            "last_name", "Utf8", "name", 0.9,
            sample_values=["smith", "jones", "garcia"],
            null_rate=0.0, cardinality_ratio=0.3, avg_len=5.0,
        ),
    ]


def _profiles_with_embeddings_and_exact():
    """Profiles that carry BOTH an embedding signal (a ``description``
    column) AND a strong, bounded exact blocking column (a moderate-
    cardinality ``identifier``). Under the #491 fallback gate the exact
    blocking key must win — ANN only fires when no bounded exact key
    exists."""
    from goldenmatch.core.autoconfig import ColumnProfile

    return [
        ColumnProfile(
            "bio", "Utf8", "description", 0.9,
            sample_values=[
                "senior cardiologist at mercy hospital",
                "pediatric nurse practitioner downtown clinic",
                "radiology technician regional medical center",
            ],
            null_rate=0.0, cardinality_ratio=0.95, avg_len=42.0,
        ),
        ColumnProfile(
            "member_id", "Utf8", "identifier", 0.9,
            sample_values=["m-100", "m-101", "m-102"],
            null_rate=0.0, cardinality_ratio=0.5, avg_len=5.0,
        ),
        ColumnProfile(
            "last_name", "Utf8", "name", 0.9,
            sample_values=["smith", "jones", "garcia"],
            null_rate=0.0, cardinality_ratio=0.3, avg_len=5.0,
        ),
    ]


def _profiles_no_embeddings():
    """Profiles with NO embedding-bearing column — must never yield ANN."""
    from goldenmatch.core.autoconfig import ColumnProfile

    return [
        ColumnProfile(
            "last_name", "Utf8", "name", 0.9,
            sample_values=["smith", "jones", "garcia"],
            null_rate=0.0, cardinality_ratio=0.3, avg_len=5.0,
        ),
        ColumnProfile(
            "city", "Utf8", "geo", 0.9,
            sample_values=["austin", "dallas", "houston"],
            null_rate=0.0, cardinality_ratio=0.2, avg_len=6.0,
        ),
    ]


def _ann_df(cols: list[str]):
    import polars as pl

    return pl.DataFrame({c: ["a", "b", "c", "d"] for c in cols})


def test_ann_blocking_when_embeddings_and_scale():
    from goldenmatch.core.autoconfig import build_blocking

    profiles = _profiles_with_embeddings()
    df = _ann_df(["bio", "last_name"])
    blk = build_blocking(profiles, df, n_rows_full=200_000)
    assert blk.strategy == "ann"
    assert blk.ann_column  # set to the embedding column name
    assert blk.ann_column == "bio"


def test_no_ann_without_embeddings():
    # Safety invariant: no embedding column -> must NOT be ann (ANNBlocker
    # would raise ValueError without an ann_column / vectors).
    from goldenmatch.core.autoconfig import build_blocking

    profiles = _profiles_no_embeddings()
    df = _ann_df(["last_name", "city"])
    blk = build_blocking(profiles, df, n_rows_full=200_000)
    assert blk.strategy != "ann"


def test_no_ann_below_scale():
    from goldenmatch.core.autoconfig import build_blocking

    profiles = _profiles_with_embeddings()
    df = _ann_df(["bio", "last_name"])
    blk = build_blocking(profiles, df, n_rows_full=1_000)
    assert blk.strategy != "ann"


def test_exact_blocking_wins_over_ann_when_available():
    # #491 fallback gate: a strong, bounded exact blocking column wins over
    # ANN even when an embedding column is present and the dataset is at
    # scale. ANN is a FALLBACK, not a preempt.
    import polars as pl
    from goldenmatch.core.autoconfig import build_blocking

    profiles = _profiles_with_embeddings_and_exact()
    # Distinct member_id per row -> singleton (bounded) exact blocks.
    df = pl.DataFrame(
        {
            "bio": ["a", "b", "c", "d"],
            "member_id": ["m-100", "m-101", "m-102", "m-103"],
            "last_name": ["smith", "jones", "garcia", "lee"],
        }
    )
    blk = build_blocking(profiles, df, n_rows_full=200_000)
    assert blk.strategy != "ann"
    # The exact key on member_id should be the chosen primary.
    fields = [f for k in (blk.keys or []) for f in k.fields]
    assert "member_id" in fields
