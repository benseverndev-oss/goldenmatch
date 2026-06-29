"""#1207 PR2a: data-driven TF name weighting."""
from __future__ import annotations

import polars as pl
from goldenmatch.core.tf_tables import value_frequencies


def test_value_frequencies_relative_and_transformed():
    df = pl.DataFrame({"last_name": ["Smith", "smith", "SMITH", "Zelinski", None, ""]})
    freqs = value_frequencies(df, "last_name", transforms=["lowercase", "strip"])
    # 3 "smith" + 1 "zelinski" over 4 non-empty -> 0.75 / 0.25; null+"" dropped
    assert abs(freqs["smith"] - 0.75) < 1e-9
    assert abs(freqs["zelinski"] - 0.25) < 1e-9
    assert "" not in freqs and None not in freqs


from goldenmatch.refdata.scorer import NameFreqWeightedJW


def test_tf_downweights_identical_common_below_identical_rare():
    s = NameFreqWeightedJW()
    tf = {"smith": 0.5, "zelinski": 0.001}   # Smith common, Zelinski rare
    common = s.score_matrix(["smith", "smith"], tf_freqs=tf)[0, 1]
    rare = s.score_matrix(["zelinski", "zelinski"], tf_freqs=tf)[0, 1]
    assert common < rare
    assert rare >= 0.99            # rare identical ~ full credit
    assert common <= 0.75          # common identical materially downweighted


def test_tf_absent_is_todays_static_behavior():
    s = NameFreqWeightedJW()
    m = s.score_matrix(["smith", "smith"])     # no tf_freqs -> static path -> plain jw
    assert abs(m[0, 1] - 1.0) < 1e-6


def test_tf_score_pair_matches_matrix():
    s = NameFreqWeightedJW()
    tf = {"smith": 0.5, "zelinski": 0.001}
    assert abs(s.score_pair("smith", "smith", tf_freqs=tf) - s.score_matrix(["smith","smith"], tf_freqs=tf)[0,1]) < 1e-6
    # mixed pair: smith (common) vs zelinski (rare), jw < 1.0
    sp = s.score_pair("smith", "zelinski", tf_freqs=tf)
    sm = s.score_matrix(["smith", "zelinski"], tf_freqs=tf)[0, 1]
    assert abs(sp - sm) < 1e-4   # float32 matrix vs float64 pair tolerance


def test_fuzzy_score_matrix_passes_tf_freqs():
    from goldenmatch.core.scorer import _fuzzy_score_matrix
    tf = {"smith": 0.5, "zelinski": 0.001}
    common = _fuzzy_score_matrix(["smith", "smith"], "name_freq_weighted_jw", tf_freqs=tf)[0, 1]
    rare = _fuzzy_score_matrix(["zelinski", "zelinski"], "name_freq_weighted_jw", tf_freqs=tf)[0, 1]
    assert common < rare


def test_fuzzy_score_matrix_tf_none_is_static():
    from goldenmatch.core.scorer import _fuzzy_score_matrix
    m = _fuzzy_score_matrix(["smith", "smith"], "name_freq_weighted_jw")
    assert abs(m[0, 1] - 1.0) < 1e-6
