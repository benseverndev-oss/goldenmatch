"""Tests for goldenmatch.config.splink_upgrade_measure -- Task U5: the
measurement stage (baseline-vs-upgraded dedupe runs on the sample, temp-file
model injection, pairwise / B-cubed metrics, snowball flag) plus its wiring
into the ``upgrade_splink_conversion`` orchestrator.
"""
from __future__ import annotations

import os

import polars as pl
import pytest
from goldenmatch.config.from_splink import from_splink
from goldenmatch.config.splink_upgrade import (
    MeasurementResult,
    PairwiseAgreement,
    RunStats,
    TruthMetrics,
    upgrade_splink_conversion,
)
from goldenmatch.config.splink_upgrade_measure import (
    bcubed,
    pair_set,
    pairwise_prf,
    snowball_flag,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _trained_jw_comparison():
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": '"first_name_l" = "first_name_r"',
                "m_probability": 0.5,
                "u_probability": 0.02,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                ),
                "m_probability": 0.3,
                "u_probability": 0.08,
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'
                ),
                "m_probability": 0.15,
                "u_probability": 0.20,
            },
            {
                "sql_condition": "ELSE",
                "m_probability": 0.05,
                "u_probability": 0.70,
            },
        ],
    }


def _trained_exact_comparison(column):
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "m_probability": 0.9,
                "u_probability": 0.05,
            },
            {"sql_condition": "ELSE", "m_probability": 0.1, "u_probability": 0.95},
        ],
    }


def _bare_jw_comparison():
    comp = _trained_jw_comparison()
    for level in comp["comparison_levels"]:
        level.pop("m_probability", None)
        level.pop("u_probability", None)
    return comp


def _bare_exact_comparison(column):
    comp = _trained_exact_comparison(column)
    for level in comp["comparison_levels"]:
        level.pop("m_probability", None)
        level.pop("u_probability", None)
    return comp


def _trained_settings():
    return {
        "comparisons": [_trained_jw_comparison(), _trained_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
        "probability_two_random_records_match": 0.01,
    }


def _bare_settings():
    return {
        "comparisons": [_bare_jw_comparison(), _bare_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
    }


# Surname stems chosen to spread across soundex codes (synthetic-fixture rule).
_SURNAME_STEMS = [
    "smith", "jones", "brown", "davis", "wilson", "moore", "taylor", "clark",
    "lewis", "walker", "hall", "allen", "young", "king", "wright", "lopez",
    "hill", "green", "adams", "baker",
]
_NAME_STEMS = [
    "alice", "bob", "carol", "dave", "erin", "frank", "grace", "henry",
    "irene", "jack", "karen", "louis", "maria", "nate", "olga", "peter",
    "quinn", "rachel", "steve", "tina",
]


def _measure_df(n_entities=20, dupes_per_entity=3):
    """60-row df with planted duplicate groups: ``n_entities`` entities, each
    duplicated ``dupes_per_entity`` times with identical first_name + surname
    (blocking on surname puts every entity in its own block)."""
    uids: list[str] = []
    first: list[str] = []
    sur: list[str] = []
    entity: list[str] = []
    j = 0
    for i in range(n_entities):
        for _ in range(dupes_per_entity):
            uids.append(f"r{j}")
            first.append(_NAME_STEMS[i % len(_NAME_STEMS)])
            sur.append(_SURNAME_STEMS[i % len(_SURNAME_STEMS)])
            entity.append(f"e{i}")
            j += 1
    return pl.DataFrame(
        {"unique_id": uids, "first_name": first, "surname": sur, "__entity__": entity}
    )


def _truth_frame(df):
    """id -> cluster_id reference frame from the planted entity column."""
    return df.select(
        pl.col("unique_id"), pl.col("__entity__").alias("cluster_id")
    )


@pytest.fixture()
def captured_tmpdirs(monkeypatch):
    """Record every tempdir the measure module creates (so tests can assert
    cleanup) without changing behavior."""
    import tempfile as _tempfile

    import goldenmatch.config.splink_upgrade_measure as measure_mod

    created: list[str] = []
    real_mkdtemp = _tempfile.mkdtemp

    def _recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        if "gm_splink_upgrade_measure" in os.path.basename(d):
            created.append(d)
        return d

    monkeypatch.setattr(measure_mod.tempfile, "mkdtemp", _recording_mkdtemp)
    return created


def _measure_data_df():
    df = _measure_df()
    return df.drop("__entity__")


# ── 1. Full path ──────────────────────────────────────────────────────────────


def test_measure_true_full_path(captured_tmpdirs):
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None
    df = _measure_data_df()

    result = upgrade_splink_conversion(conversion, df, measure=True)

    m = result.measurement
    assert isinstance(m, MeasurementResult)
    assert m.sample_rows == len(df)
    assert m.sampled is False
    for stats in (m.baseline, m.upgraded):
        assert isinstance(stats, RunStats)
        assert isinstance(stats.cluster_count, int)
        assert isinstance(stats.multi_record_clusters, int)
        assert isinstance(stats.max_cluster_size, int)
        assert isinstance(stats.singleton_count, int)
        assert stats.cluster_count > 0
        assert isinstance(stats.wall_seconds, float)
        assert stats.wall_seconds > 0.0

    # Temp model files were created for the trained input and cleaned up.
    assert len(captured_tmpdirs) == 1
    assert not os.path.exists(captured_tmpdirs[0])

    # The returned upgraded config must NOT carry a temp model_path.
    for mk in result.upgraded_config.get_matchkeys():
        assert mk.model_path is None
    for mk in result.baseline_config.get_matchkeys():
        assert mk.model_path is None


# ── 2. vs_splink ─────────────────────────────────────────────────────────────


def test_splink_clusters_reference_yields_pairwise_agreement():
    conversion = from_splink(_trained_settings())
    df = _measure_df()
    splink_ref = _truth_frame(df)

    result = upgrade_splink_conversion(
        conversion, df.drop("__entity__"), splink_clusters=splink_ref, measure=True
    )

    m = result.measurement
    assert m is not None
    vs = m.vs_splink
    assert isinstance(vs, PairwiseAgreement)
    for metrics in (vs.baseline, vs.upgraded):
        for key in ("precision", "recall", "f1"):
            assert 0.0 <= metrics[key] <= 1.0


# ── 3. vs_labels ─────────────────────────────────────────────────────────────


def test_labels_reference_yields_truth_metrics():
    conversion = from_splink(_trained_settings())
    df = _measure_df()
    labels = _truth_frame(df)

    result = upgrade_splink_conversion(
        conversion, df.drop("__entity__"), labels=labels, measure=True
    )

    m = result.measurement
    assert m is not None
    vt = m.vs_labels
    assert isinstance(vt, TruthMetrics)
    for metrics in (vt.baseline, vt.upgraded):
        for key in (
            "pairwise_precision", "pairwise_recall", "pairwise_f1",
            "bcubed_precision", "bcubed_recall", "bcubed_f1",
        ):
            assert 0.0 <= metrics[key] <= 1.0


# ── 3b. Zero-overlap references: refuse the id join, don't report 0.0 ────────


def test_zero_overlap_splink_clusters_yields_none_and_warning():
    conversion = from_splink(_trained_settings())
    df = _measure_data_df()  # ids r0..r59
    bogus_ref = pl.DataFrame(
        {
            "unique_id": [f"x{i}" for i in range(len(df))],
            "cluster_id": [f"e{i // 3}" for i in range(len(df))],
        }
    )

    result = upgrade_splink_conversion(
        conversion, df, splink_clusters=bogus_ref, measure=True
    )

    m = result.measurement
    assert m is not None
    assert m.vs_splink is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "warning"
        and "splink_clusters" in f.message
    ]
    assert len(warn_findings) == 1
    assert "id_column" in warn_findings[0].message
    assert "unique_id" in warn_findings[0].message  # names the id source used


def test_zero_overlap_labels_yields_none_and_warning():
    conversion = from_splink(_trained_settings())
    df = _measure_data_df()
    bogus_labels = pl.DataFrame(
        {
            "unique_id": [f"x{i}" for i in range(len(df))],
            "cluster_id": [f"e{i // 3}" for i in range(len(df))],
        }
    )

    result = upgrade_splink_conversion(
        conversion, df, labels=bogus_labels, measure=True
    )

    m = result.measurement
    assert m is not None
    assert m.vs_labels is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "warning"
        and "labels" in f.message
    ]
    assert len(warn_findings) == 1
    assert "id_column" in warn_findings[0].message


def test_empty_reference_yields_none_and_warning():
    """A ZERO-ROW reference must get the same treatment as a zero-overlap
    one (warning + absent metrics block) -- it previously slipped past the
    zero-overlap refusal's ``n_rows > 0`` guard and produced all-0.0
    metrics."""
    conversion = from_splink(_trained_settings())
    df = _measure_data_df()
    empty_ref = pl.DataFrame(
        schema={"unique_id": pl.Utf8, "cluster_id": pl.Utf8}
    )

    result = upgrade_splink_conversion(
        conversion, df, splink_clusters=empty_ref, measure=True
    )

    m = result.measurement
    assert m is not None
    assert m.vs_splink is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "warning"
        and "splink_clusters" in f.message
    ]
    assert len(warn_findings) == 1
    assert "empty" in warn_findings[0].message


def test_partial_overlap_reference_still_computes():
    conversion = from_splink(_trained_settings())
    df = _measure_df()
    # Reference covering only the first half of the sample ids.
    half = _truth_frame(df).head(len(df) // 2)

    result = upgrade_splink_conversion(
        conversion, df.drop("__entity__"), splink_clusters=half, measure=True
    )

    m = result.measurement
    assert m is not None
    vs = m.vs_splink
    assert isinstance(vs, PairwiseAgreement)
    for metrics in (vs.baseline, vs.upgraded):
        for key in ("precision", "recall", "f1"):
            assert 0.0 <= metrics[key] <= 1.0
    # No zero-overlap warning on the partial path.
    assert not any(
        f.splink_path == "upgrade:measure" and f.severity == "warning"
        and "shares no ids" in f.message
        for f in result.report.findings
    )


# ── 4. No reference: shape-only ──────────────────────────────────────────────


def test_no_reference_is_shape_only():
    conversion = from_splink(_trained_settings())

    result = upgrade_splink_conversion(conversion, _measure_data_df(), measure=True)

    m = result.measurement
    assert m is not None
    assert m.vs_splink is None
    assert m.vs_labels is None
    info_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "info"
        and "shape-only" in f.message
    ]
    assert len(info_findings) == 1


# ── 5. Dedupe crash: transform-only downgrade ────────────────────────────────


def test_dedupe_crash_downgrades_to_transform_only(monkeypatch, captured_tmpdirs):
    import goldenmatch._api as api_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic dedupe crash")

    monkeypatch.setattr(api_mod, "dedupe_df", _boom)
    conversion = from_splink(_trained_settings())

    result = upgrade_splink_conversion(conversion, _measure_data_df(), measure=True)

    assert result.measurement is None
    # Upgraded config still returned (transform-only).
    assert result.upgraded_config is not None
    error_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "error"
    ]
    assert len(error_findings) == 1
    assert "synthetic dedupe crash" in error_findings[0].message
    # Temp model files cleaned up even on the crash path.
    assert len(captured_tmpdirs) == 1
    assert not os.path.exists(captured_tmpdirs[0])


# ── 6. Bare settings: measurement still runs ─────────────────────────────────


def test_bare_settings_measurement_runs_with_note(captured_tmpdirs):
    conversion = from_splink(_bare_settings())
    assert conversion.em_model is None

    result = upgrade_splink_conversion(conversion, _measure_data_df(), measure=True)

    m = result.measurement
    assert isinstance(m, MeasurementResult)
    assert m.baseline.cluster_count > 0
    assert m.upgraded.cluster_count > 0
    note_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:measure" and f.severity == "info"
        and "imported-model" in f.message
    ]
    assert len(note_findings) == 1
    # No model files -> no tempdir needed.
    assert captured_tmpdirs == []


# ── 7. B-cubed (pure function, ported from the bench orchestrator) ───────────


def test_bcubed_hand_computed_six_records():
    pred = {"a": "1", "b": "1", "c": "1", "d": "2", "e": "2", "f": "3"}
    true = {"a": "x", "b": "x", "c": "y", "d": "y", "e": "z", "f": "z"}

    out = bcubed(pred, true)

    # Per-item precision: a,b: 2/3; c: 1/3; d,e: 1/2; f: 1.
    expected_p = (2 / 3 + 2 / 3 + 1 / 3 + 1 / 2 + 1 / 2 + 1.0) / 6
    # Per-item recall: a,b: 1; c,d,e,f: 1/2.
    expected_r = (1.0 + 1.0 + 0.5 + 0.5 + 0.5 + 0.5) / 6
    expected_f1 = 2 * expected_p * expected_r / (expected_p + expected_r)
    assert out["precision"] == pytest.approx(expected_p)
    assert out["recall"] == pytest.approx(expected_r)
    assert out["f1"] == pytest.approx(expected_f1)


def test_bcubed_perfect_clustering():
    mapping = {"a": "1", "b": "1", "c": "2"}
    out = bcubed(mapping, {"a": "x", "b": "x", "c": "y"})
    assert out == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_bcubed_ignores_ids_missing_from_truth():
    pred = {"a": "1", "b": "1", "ghost": "1"}
    true = {"a": "x", "b": "x"}
    out = bcubed(pred, true)
    # ghost inflates cluster "1" to 3 members; a and b each score p=2/3, r=1.
    assert out["precision"] == pytest.approx(2 / 3)
    assert out["recall"] == pytest.approx(1.0)


def test_pair_set_and_prf():
    pred_pairs, capped = pair_set({"a": "1", "b": "1", "c": "1", "d": "2"})
    assert capped == 0
    assert pred_pairs == {("a", "b"), ("a", "c"), ("b", "c")}

    true_pairs, _ = pair_set({"a": "x", "b": "x", "c": "y", "d": "y"})
    out = pairwise_prf(pred_pairs, true_pairs)
    # tp = {(a,b)}; pred has 3 pairs, true has 2.
    assert out["precision"] == pytest.approx(1 / 3)
    assert out["recall"] == pytest.approx(1 / 2)
    assert out["f1"] == pytest.approx(2 * (1 / 3) * (1 / 2) / (1 / 3 + 1 / 2))


def test_pair_set_caps_giant_clusters():
    mapping = {f"m{i}": "giant" for i in range(6000)}
    mapping["a"] = "small"
    mapping["b"] = "small"
    pairs, capped = pair_set(mapping, cap=5000)
    assert capped == 1
    assert pairs == {("a", "b")}


# ── 8. Snowball flag ─────────────────────────────────────────────────────────


def test_snowball_flag_vs_reference_max():
    assert snowball_flag([1, 1, 55], reference_max=5) is True
    assert snowball_flag([1, 1, 50], reference_max=5) is False  # not strictly >
    assert snowball_flag([1, 1, 51], reference_max=5) is True


def test_snowball_flag_own_p99_reference():
    # 100 singletons + one 200-member cluster: p99 of the run's own sizes is 1,
    # so 200 > 10x1 flags.
    sizes = [1] * 100 + [200]
    assert snowball_flag(sizes) is True
    # Uniform sizes never flag against their own p99.
    assert snowball_flag([3] * 50) is False


def test_snowball_flag_empty_and_degenerate():
    assert snowball_flag([]) is False
    assert snowball_flag([1, 2, 3], reference_max=0) is False
