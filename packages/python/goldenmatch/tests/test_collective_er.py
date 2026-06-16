"""Tests for collective entity resolution features."""
from tests.collective_er.metrics import pairwise_prf


def test_pairwise_prf_perfect():
    """Perfect clustering should yield F1=1.0."""
    truth = {0: "A", 1: "A", 2: "B"}          # record_id -> true entity
    clusters = {0: 0, 1: 0, 2: 1}             # record_id -> predicted cluster
    p, r, f = pairwise_prf(clusters, truth)
    assert (p, r, f) == (1.0, 1.0, 1.0)


def test_pairwise_prf_over_merge():
    """Over-merging should hurt precision while keeping recall full."""
    truth = {0: "A", 1: "A", 2: "B"}
    clusters = {0: 0, 1: 0, 2: 0}             # wrongly merged 2 with A
    p, r, f = pairwise_prf(clusters, truth)
    assert r == 1.0 and p < 1.0               # recall full, precision hurt


# ---------------------------------------------------------------------------
# Task 2: relational fixture generator
# ---------------------------------------------------------------------------

from tests.collective_er.fixture import generate_relational_fixture  # noqa: E402


def test_fixture_is_deterministic_and_ambiguous():
    f1 = generate_relational_fixture(seed=7, n_entities=20)
    f2 = generate_relational_fixture(seed=7, n_entities=20)
    assert f1.authors.to_dicts() == f2.authors.to_dicts()       # deterministic

    name_to_truth: dict = {}
    for r in f1.authors.iter_rows(named=True):
        name_to_truth.setdefault(r["name"], set()).add(r["author_truth"])
    assert any(len(v) > 1 for v in name_to_truth.values())       # genuine homonyms

    assert set(f1.truth) == set(f1.authors["__row_id__"].to_list())  # truth covers all rows


def test_fixture_schema():
    """Authors, papers, authorship columns are exactly right."""
    f = generate_relational_fixture(seed=42, n_entities=10)
    assert f.authors.columns == ["__row_id__", "name", "author_truth"]
    assert f.papers.columns == ["__row_id__", "paper_id"]
    assert f.authorship.columns == ["paper_row_id", "author_row_id"]


def test_fixture_authorship_referential_integrity():
    """Every authorship edge points to a valid author and paper row_id."""
    f = generate_relational_fixture(seed=1, n_entities=15)
    author_ids = set(f.authors["__row_id__"].to_list())
    paper_ids = set(f.papers["__row_id__"].to_list())
    for row in f.authorship.iter_rows(named=True):
        assert row["author_row_id"] in author_ids
        assert row["paper_row_id"] in paper_ids


def test_fixture_synonyms_present():
    """At least one entity should have multiple distinct name variants."""
    f = generate_relational_fixture(seed=3, n_entities=20)
    entity_to_names: dict = {}
    for r in f.authors.iter_rows(named=True):
        entity_to_names.setdefault(r["author_truth"], set()).add(r["name"])
    assert any(len(v) > 1 for v in entity_to_names.values())


def test_fixture_truth_completeness():
    """Truth dict covers every author row and maps to the right entity."""
    f = generate_relational_fixture(seed=99, n_entities=10)
    for row in f.authors.iter_rows(named=True):
        rid = row["__row_id__"]
        assert rid in f.truth
        assert f.truth[rid] == row["author_truth"]


# ---------------------------------------------------------------------------
# Task 3: Phase-0 baseline gate
#
# Measured numbers (seed=7, n_entities=40, default fixture params:
#   homonym_rate=0.30, synonym_rate=0.30, papers_per_author=3,
#   coauthors_per_paper=2):
#
#   independent (attribute-only): P=0.585  R=0.813  F1=0.681
#   flat-boost (graph ER additive): P=0.031  R=0.777  F1=0.059
#
# No fixture calibration needed: defaults already give attribute-only F1=0.681,
# well below the 0.85 gate.
#
# The flat-boost is deliberately naive (boosts ALL co-author pairs on shared
# papers, regardless of entity identity), so precision collapses to 0.031.
# Task 8 replaces this with proper co-author neighborhood alignment.
# ---------------------------------------------------------------------------

def _author_config():
    """Minimal offline-safe GoldenMatchConfig for author name deduplication.

    Uses a single weighted matchkey on `name` with jaro_winkler + soundex
    blocking. rerank=False avoids the cross-encoder model download.
    """
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="name_fuzzy",
        type="weighted",
        threshold=0.80,
        rerank=False,
        fields=[
            MatchkeyField(
                field="name",
                scorer="jaro_winkler",
                weight=1.0,
                transforms=["lowercase", "strip"],
            )
        ],
    )
    blocking = BlockingConfig(
        keys=[
            BlockingKeyConfig(
                fields=["name"],
                transforms=["soundex"],
            )
        ],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def _independent_author_f1(fixture, tmp_path):
    """Run attribute-only ER on author names, return (precision, recall, F1)."""
    import goldenmatch

    cfg = _author_config()
    authors_df = fixture.authors.select(["__row_id__", "name"])

    result = goldenmatch.dedupe_df(authors_df, config=cfg)

    # Invert clusters: member_row_id -> cluster_id
    pred = {}
    for cid, cinfo in result.clusters.items():
        for mid in cinfo["members"]:
            pred[mid] = cid
    # Singletons: any author row not in a multi-member cluster
    all_ids = authors_df["__row_id__"].to_list()
    next_singleton = max(pred.values(), default=-1) + 1
    for rid in all_ids:
        if rid not in pred:
            pred[rid] = next_singleton
            next_singleton += 1

    return pairwise_prf(pred, fixture.truth)


def _flatboost_author_f1(fixture, tmp_path):
    """Run graph ER with additive evidence propagation; return (P, R, F1).

    Entity graph:
    - `author`: the authors CSV (deduped on name).
    - `paper`: the authorship CSV denormalized with paper_id
      (columns: __row_id__[=paper_row_id], paper_id, author_row_id).
      Dedupe on paper_id (exact) groups membership rows for the same paper.
    - Relationship from_entity=`paper`, to_entity=`author`,
      join_key=`author_row_id`.
      This propagates: "two authors who share papers should get score boosts."
    """
    import polars as pl
    import goldenmatch
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.graph_er import EntityType, Relationship, run_graph_er

    # --- Write author CSV ---
    # Include author_row_id column (= __row_id__) so _propagate_evidence can
    # match paper.author_row_id -> author.author_row_id (the join key).
    authors_for_csv = fixture.authors.select(["__row_id__", "name"]).with_columns(
        pl.col("__row_id__").alias("author_row_id")
    )
    author_csv = str(tmp_path / "authors.csv")
    authors_for_csv.write_csv(author_csv)

    # --- Build denormalized paper+authorship CSV ---
    # Join authorship to papers to get paper_id on each authorship row.
    # Each row gets a unique __row_id__ (row index of the authorship table).
    # Columns: __row_id__ (unique per row), paper_id, author_row_id, paper_row_id
    authorship_w_pid = (
        fixture.authorship.join(
            fixture.papers.rename({"__row_id__": "paper_row_id"}),
            on="paper_row_id",
            how="left",
        )
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    paper_csv = str(tmp_path / "paper_authorship.csv")
    authorship_w_pid.write_csv(paper_csv)

    # --- Configs ---
    author_cfg = _author_config()

    # Paper entity: exact match on paper_id (groups membership rows per paper)
    paper_mk = MatchkeyConfig(
        name="paper_exact",
        type="exact",
        fields=[MatchkeyField(field="paper_id", transforms=["strip"])],
    )
    paper_blocking = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["paper_id"], transforms=[])],
    )
    paper_cfg = GoldenMatchConfig(matchkeys=[paper_mk], blocking=paper_blocking)

    # --- Entity types ---
    author_entity = EntityType(
        name="author",
        sources=[(author_csv, "authors")],
        config=author_cfg,
    )
    paper_entity = EntityType(
        name="paper",
        sources=[(paper_csv, "paper_authorship")],
        config=paper_cfg,
    )

    # Relationship: paper clusters -> boost author pairs via author_row_id FK
    rel = Relationship(
        from_entity="paper",
        to_entity="author",
        join_key="author_row_id",
        evidence_weight=0.4,
    )

    result = run_graph_er(
        entities=[author_entity, paper_entity],
        relationships=[rel],
        max_iterations=3,
        propagation_mode="additive",
    )

    author_et = result.entities["author"]
    # Invert clusters
    pred = {}
    for cid, cinfo in author_et.clusters.items():
        for mid in cinfo["members"]:
            pred[mid] = cid
    # Singletons
    all_ids = fixture.authors["__row_id__"].to_list()
    next_singleton = max(pred.values(), default=-1) + 1
    for rid in all_ids:
        if rid not in pred:
            pred[rid] = next_singleton
            next_singleton += 1

    return pairwise_prf(pred, fixture.truth)


def test_phase0_attribute_only_is_weak(tmp_path):
    """Attribute-only ER must score below F1=0.85 on the relational fixture.

    This proves the fixture is genuinely hard -- names alone cannot resolve it.
    The moat: homonyms force false-merges and synonyms force false-splits; only
    co-author neighborhood (relational) evidence can break the tie.

    Measured at seed=7, n_entities=40, default fixture rates (homonym 0.30,
    synonym 0.30): independent F1=0.681, and flat-boost F1=0.681 (the current
    graph_er adds nothing on this co-authorship fixture) -- a ~17pt gap below
    0.85 for collective ER to close.
    """
    fx = generate_relational_fixture(seed=7, n_entities=40)
    p, r, f = _independent_author_f1(fx, tmp_path)
    # Record for reference (visible in verbose pytest output)
    print(f"\n[phase-0] independent: P={p:.3f} R={r:.3f} F1={f:.3f}")
    assert f < 0.85, (
        f"Attribute-only F1={f:.3f} is NOT below 0.85. "
        "The fixture may need higher homonym_rate or synonym_rate to widen the moat."
    )


def test_phase0_flatboost_baseline_runs(tmp_path):
    """Flat-boost (graph ER with additive propagation) baseline runs without error.

    Records the F1 so Task 8 can measure the improvement over this baseline.
    Gate: 0.0 <= F1 <= 1.0 (sanity only -- quality improvement is Task 8).
    """
    fx = generate_relational_fixture(seed=7, n_entities=40)
    p, r, f = _flatboost_author_f1(fx, tmp_path)
    print(f"\n[phase-0] flat-boost: P={p:.3f} R={r:.3f} F1={f:.3f}")
    assert 0.0 <= f <= 1.0, f"Flat-boost F1={f:.3f} is out of [0,1] range (unexpected)"
