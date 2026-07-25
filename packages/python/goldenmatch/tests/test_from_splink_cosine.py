"""Splink CosineSimilarity conversion -> the `cosine` scorer.

`CosineSimilarityAtThresholds` compares two PRECOMPUTED vector columns:
`array_cosine_similarity("vec_l", "vec_r") >= t` (captured live from splink 4).
GoldenMatch's `cosine` scorer is the same measure over parsed float vectors, so
the threshold maps DIRECTLY -- NOT an approximation (unlike the banded domain
comparators). Unlike the `embedding` scorer (which embeds text via a model at
score time), `cosine` compares vectors the data already carries.
"""
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    convert_comparison,
    import_em,
    recognize_level,
)
from goldenmatch.config.schemas import _is_valid_scorer
from goldenmatch.core.scorer import score_field

# --- the cosine scorer -------------------------------------------------------


def test_cosine_scorer_basic():
    assert score_field("0.1,0.2,0.3", "0.1,0.2,0.3", "cosine") == 1.0   # identical
    assert score_field("1,0", "0,1", "cosine") == 0.0                    # orthogonal
    assert score_field("1,2,3", "2,4,6", "cosine") == pytest.approx(1.0)  # parallel
    # negatives clamp to 0 (GoldenMatch sims live in [0,1]; no `>= t` decision for
    # t in (0,1] changes).
    assert score_field("1,0", "-1,0", "cosine") == 0.0


def test_cosine_scorer_parse_variants():
    # bracketed + whitespace-separated both parse.
    assert score_field("[1, 2, 3]", "[2, 4, 6]", "cosine") == pytest.approx(1.0)
    assert score_field("1 2 3", "2 4 6", "cosine") == pytest.approx(1.0)


def test_cosine_scorer_fallback_on_unparseable():
    # length mismatch / non-numeric / zero-norm -> exact-string fallback (never None).
    assert score_field("1,2", "1,2,3", "cosine") == 0.0        # mismatched length
    assert score_field("a,b", "a,b", "cosine") == 1.0          # unparseable but equal
    assert score_field("0,0", "0,0", "cosine") == 1.0          # zero norm but equal
    assert score_field("0,0", "1,1", "cosine") == 0.0          # zero norm, unequal


def test_cosine_scalar_equals_vectorized():
    # The NxN matrix path must equal the scalar path (it calls the same fn).
    from goldenmatch.core.scorer import _fuzzy_score_matrix

    vals = ["1,0,0", "0,1,0", "1,1,0", "0.7,0.7,0", ""]
    m = _fuzzy_score_matrix(vals, "cosine")
    # Off-diagonal only: like every domain-comparator matrix, the diagonal (a row
    # vs itself) is left 0 and never used in pair scoring.
    for i in range(len(vals)):
        for j in range(len(vals)):
            if i != j and vals[i] and vals[j]:
                assert m[i, j] == pytest.approx(
                    score_field(vals[i], vals[j], "cosine"), abs=1e-6
                ), (vals[i], vals[j])


def test_cosine_is_a_valid_scorer():
    assert _is_valid_scorer("cosine")


# --- recognizer + conversion -------------------------------------------------


@pytest.mark.parametrize(
    "sql,threshold",
    [
        ('array_cosine_similarity("vec_l", "vec_r") >= 0.9', 0.9),
        ("array_cosine_similarity(`vec_l`, `vec_r`) >= 0.75", 0.75),   # Spark backtick
        ('ARRAY_COSINE_SIMILARITY("vec_l", "vec_r") >= 0.8', 0.8),      # case-insensitive
    ],
)
def test_cosine_recognized(sql, threshold):
    r = recognize_level(sql)
    assert r is not None
    assert r.kind == "cosine"
    assert r.column == "vec"
    assert r.sim_threshold == pytest.approx(threshold)
    assert r.approx is False          # DIRECT threshold mapping, not an approximation


def test_cosine_mismatched_columns_dropped():
    assert recognize_level('array_cosine_similarity("a_l", "b_r") >= 0.9') is None


def _cosine_comparison(thresholds, col="vec"):
    levels = [
        {"sql_condition": f'"{col}_l" IS NULL OR "{col}_r" IS NULL', "is_null_level": True},
        {"sql_condition": f'"{col}_l" = "{col}_r"'},
    ]
    levels += [
        {"sql_condition": f'array_cosine_similarity("{col}_l", "{col}_r") >= {t}'}
        for t in thresholds
    ]
    levels.append({"sql_condition": "ELSE"})
    return {"output_column_name": col, "comparison_levels": levels}


def test_cosine_comparison_converts_exactly():
    report = ConversionReport()
    field = convert_comparison(_cosine_comparison([0.9, 0.7]), 0, report)

    assert field is not None
    assert field.field == "vec"
    assert field.scorer == "cosine"
    # exact (1.0) + the two cosine thresholds, mapped DIRECTLY (no approximation).
    assert field.levels == 4
    assert field.level_thresholds == [1.0, 0.9, 0.7]
    # no approximate-mapping warn (cosine threshold == the scorer's own output).
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert not any("approximate" in w.lower() for w in warns)


def test_trained_cosine_import_maps_every_level():
    comp = _cosine_comparison([0.9, 0.7])
    # levels: 0 null, 1 exact, 2 >=0.9, 3 >=0.7, 4 ELSE
    mu = {1: (0.6, 0.01), 2: (0.2, 0.04), 3: (0.15, 0.15), 4: (0.05, 0.80)}
    for idx, (m, u) in mu.items():
        comp["comparison_levels"][idx]["m_probability"] = m
        comp["comparison_levels"][idx]["u_probability"] = u
    settings = {"comparisons": [comp], "probability_two_random_records_match": 0.0002}
    report = ConversionReport()

    field = convert_comparison(comp, 0, report)
    assert field is not None and field.levels == 4
    em = import_em([(comp, 0, field)], settings, report)
    assert em is not None
    m = em.m_probs["vec"]
    assert len(m) == 4
    assert m == pytest.approx([0.05, 0.15, 0.2, 0.6])   # else < .7 < .9 < exact
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert not any("does not match any converted threshold" in w for w in warns)
