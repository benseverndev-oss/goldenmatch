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


# --- Task A4: auto-config populates tf_freqs + end-to-end proof ---

from goldenmatch.core.autoconfig import auto_configure_df


def _common_vs_rare_name_df(n=800, seed=7):
    import random
    rng = random.Random(seed)
    commons = ["Smith", "Jones", "Brown"]
    rares = [f"Rarename{i}" for i in range(60)]
    rows = [{"first_name": rng.choice(["John", "Jane", "Mary", "Alex"]),
             "last_name": rng.choice(commons) if rng.random() < 0.7 else rng.choice(rares),
             "city": rng.choice(["Springfield", "Madison", "Fairview"])}
            for _ in range(n)]
    return pl.DataFrame(rows)


def _name_fields(cfg):
    return [f for mk in cfg.get_matchkeys() for f in (mk.fields or [])
            if getattr(f, "scorer", None) == "name_freq_weighted_jw"]


def test_autoconfig_populates_tf_freqs_for_name_scorer(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TF_NAME_WEIGHTING", "1")
    cfg = auto_configure_df(_common_vs_rare_name_df())
    nf = _name_fields(cfg)
    assert nf, "expected a name_freq_weighted_jw field on this person shape"
    assert any(getattr(f, "tf_freqs", None) for f in nf), "tf_freqs not populated"


def test_kill_switch_disables_tf_population(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TF_NAME_WEIGHTING", "0")
    cfg = auto_configure_df(_common_vs_rare_name_df())
    fields = [f for mk in cfg.get_matchkeys() for f in (mk.fields or [])]
    assert all(getattr(f, "tf_freqs", None) is None for f in fields)


def test_tf_downweight_reaches_scoring_end_to_end(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_TF_NAME_WEIGHTING", "1")
    df = _common_vs_rare_name_df()
    cfg = auto_configure_df(df)
    nf = _name_fields(cfg)
    if not nf:
        import pytest
        pytest.skip("name_freq_weighted_jw not selected on this shape")

    # The populated table on the configured last_name field, scored through the
    # SAME scorer the pipeline uses. If standardization mangled key alignment the
    # common surname would NOT score below the rare one.
    field = nf[0]
    tf = field.tf_freqs
    assert tf, "tf_freqs missing on configured field"

    from goldenmatch.core.scorer import _fuzzy_score_matrix
    from goldenmatch.core.standardize import get_standardizer
    from goldenmatch.utils.transforms import apply_transforms

    # Reproduce the pipeline's value-at-score-time: standardize (per the config's
    # standardization rules) THEN apply the matchkey transforms. If the populated
    # table (built at config time) didn't account for this, no key would match and
    # the downweight would be dormant -> common would NOT score below rare.
    std_rules = (cfg.standardization.rules or {}).get(field.field, []) if cfg.standardization else []

    def _scored(raw):
        v = raw
        for s in std_rules:
            v = get_standardizer(s)(v)
        return apply_transforms(v, field.transforms)

    common_val = _scored("Smith")
    rare_val = _scored("Rarename1")

    common_pair_score = _fuzzy_score_matrix(
        [common_val, common_val], "name_freq_weighted_jw", tf_freqs=tf
    )[0, 1]
    rare_pair_score = _fuzzy_score_matrix(
        [rare_val, rare_val], "name_freq_weighted_jw", tf_freqs=tf
    )[0, 1]

    assert common_pair_score < rare_pair_score
