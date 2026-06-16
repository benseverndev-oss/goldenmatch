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
