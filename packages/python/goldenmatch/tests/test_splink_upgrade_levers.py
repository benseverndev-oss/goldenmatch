"""Tests for goldenmatch.config.splink_upgrade -- Task U1 scaffold:
dataclasses, sampling, upfront column validation, and lever dispatch/skip
semantics. The three lever bodies are stubs until Tasks U2-U4; these tests
only exercise paths that don't hit the stubs (bare-settings fixtures,
``levers=set()``/subset selection, ``measure=False``).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.from_splink import from_splink
from goldenmatch.config.splink_upgrade import (
    MigrationResult,
    SplinkUpgradeError,
    upgrade_splink_conversion,
)


def _jw_comparison():
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {
                "sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": '"first_name_l" = "first_name_r"'},
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'
                )
            },
            {
                "sql_condition": (
                    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'
                )
            },
            {"sql_condition": "ELSE"},
        ],
    }


def _exact_only_comparison(column="surname"):
    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
                "is_null_level": True,
            },
            {"sql_condition": f'"{column}_l" = "{column}_r"'},
            {"sql_condition": "ELSE"},
        ],
    }


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


def _bare_settings():
    return {
        "comparisons": [_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


def _trained_settings():
    return {
        "comparisons": [_trained_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
        "probability_two_random_records_match": 0.0002,
    }


def _sample_df(n=30):
    first_names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    surnames = ["smith", "jones", "brown", "davis", "wilson", "moore"]
    return pl.DataFrame(
        {
            "first_name": [first_names[i % len(first_names)] for i in range(n)],
            "surname": [surnames[i % len(surnames)] for i in range(n)],
        }
    )


# ── Basic shape / copy-on-write ──────────────────────────────────────────────


def test_returns_migration_result_and_conversion_unmutated():
    conversion = from_splink(_bare_settings())
    baseline_dump_before = conversion.config.model_dump()
    findings_len_before = len(conversion.report.findings)

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"tf_tables", "calibration"}, measure=False
    )

    assert isinstance(result, MigrationResult)
    assert result.baseline_config.model_dump() == baseline_dump_before
    # Input conversion itself must be untouched.
    assert conversion.config.model_dump() == baseline_dump_before
    assert len(conversion.report.findings) == findings_len_before


def test_upgraded_config_is_distinct_object():
    conversion = from_splink(_bare_settings())
    baseline_dump_before = conversion.config.model_dump()

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"tf_tables", "calibration"}, measure=False
    )

    assert result.upgraded_config is not result.baseline_config
    # Mutate a field list on the upgraded copy; baseline must be unaffected.
    result.upgraded_config.get_matchkeys()[0].fields.append(
        result.upgraded_config.get_matchkeys()[0].fields[0].model_copy()
    )
    assert conversion.config.model_dump() == baseline_dump_before
    assert len(result.upgraded_config.get_matchkeys()[0].fields) != len(
        conversion.config.get_matchkeys()[0].fields
    )


def test_em_model_copy_is_deep():
    """EMResult.to_dict()/from_dict() passes nested dicts/lists by reference,
    so the orchestrator must deepcopy -- an in-place mutation of the returned
    model's nested containers must never leak into the input conversion's."""
    conversion = from_splink(_trained_settings())
    assert conversion.em_model is not None
    original_m_probs = [*conversion.em_model.m_probs["first_name"]]

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers=set(), measure=False
    )

    assert result.em_model is not None
    assert result.em_model is not conversion.em_model
    # Nested containers must be distinct objects, not shared references.
    assert result.em_model.m_probs is not conversion.em_model.m_probs
    assert (
        result.em_model.m_probs["first_name"]
        is not conversion.em_model.m_probs["first_name"]
    )
    assert (
        result.em_model.match_weights["first_name"]
        is not conversion.em_model.match_weights["first_name"]
    )
    # In-place mutation of the copy must not alter the original.
    result.em_model.m_probs["first_name"][0] = 0.999
    result.em_model.match_weights["injected"] = [1.0]
    assert conversion.em_model.m_probs["first_name"] == original_m_probs
    assert "injected" not in conversion.em_model.match_weights


# ── Column validation ─────────────────────────────────────────────────────────


def test_missing_column_raises_naming_it():
    conversion = from_splink(_bare_settings())
    df = _sample_df().drop("surname")

    with pytest.raises(SplinkUpgradeError) as exc_info:
        upgrade_splink_conversion(conversion, df, measure=False, levers=set())

    assert "surname" in str(exc_info.value)


# ── Data loading ──────────────────────────────────────────────────────────────


def test_parquet_path_input_works(tmp_path):
    conversion = from_splink(_bare_settings())
    p = tmp_path / "data.parquet"
    _sample_df().write_parquet(p)

    result = upgrade_splink_conversion(conversion, p, measure=False, levers=set())
    assert isinstance(result, MigrationResult)


# ── Sampling determinism ──────────────────────────────────────────────────────


def test_sampling_deterministic_across_calls():
    conversion = from_splink(_bare_settings())
    df = _sample_df(30)

    result1 = upgrade_splink_conversion(
        conversion, df, sample_cap=10, seed=42, measure=False, levers=set()
    )
    result2 = upgrade_splink_conversion(
        conversion, df, sample_cap=10, seed=42, measure=False, levers=set()
    )

    sample_findings_1 = [f for f in result1.report.findings if f.splink_path == "upgrade:sample"]
    sample_findings_2 = [f for f in result2.report.findings if f.splink_path == "upgrade:sample"]
    assert sample_findings_1 and sample_findings_2
    assert sample_findings_1[0].message == sample_findings_2[0].message


def test_sampling_note_only_when_over_cap():
    conversion = from_splink(_bare_settings())
    df = _sample_df(30)

    result = upgrade_splink_conversion(
        conversion, df, sample_cap=1000, measure=False, levers=set()
    )
    assert not any(f.splink_path == "upgrade:sample" for f in result.report.findings)


# ── Bare-settings skip semantics ──────────────────────────────────────────────


def test_bare_settings_skips_tf_tables_and_calibration():
    conversion = from_splink(_bare_settings())
    assert conversion.em_model is None

    result = upgrade_splink_conversion(
        conversion,
        _sample_df(),
        levers={"tf_tables", "calibration"},
        measure=False,
    )

    tf_findings = [f for f in result.report.findings if f.splink_path == "upgrade:tf_tables"]
    calib_findings = [f for f in result.report.findings if f.splink_path == "upgrade:calibration"]
    assert tf_findings and tf_findings[0].severity == "info"
    assert "skipped" in tf_findings[0].message.lower()
    assert calib_findings and calib_findings[0].severity == "info"
    assert "skipped" in calib_findings[0].message.lower()


def test_bare_settings_distance_thresholds_lever_still_applies():
    """Bare-settings inputs skip levers 1/3 but lever 2 still applies --
    band thresholds are config-level, fixed before training (spec)."""
    conversion = from_splink(_bare_settings_with_levenshtein())
    assert conversion.em_model is None

    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20),
        levers={"distance_thresholds"},
        measure=False,
    )

    field = _field(result.upgraded_config, "email")
    assert field.level_thresholds == pytest.approx([0.95, 0.85])
    assert result.em_model is None


# ── Lever 1: TF tables ────────────────────────────────────────────────────────


def _trained_settings_with_tf_adjustment():
    """Trained settings whose first_name comparison's exact-match level
    carries ``tf_adjustment_column`` -- converts to a field with
    ``tf_adjustment=True`` (surname stays plain, no tf_adjustment)."""
    comp = _trained_jw_comparison()
    comp["comparison_levels"][1]["tf_adjustment_column"] = "first_name"
    return {
        "comparisons": [comp, _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
        "probability_two_random_records_match": 0.0002,
    }


def _skewed_tf_df(n=40):
    """40 rows with a skewed first_name distribution (alice dominates) and a
    surname column that never gets a TF table (no tf_adjustment on it)."""
    first_names = ["alice"] * 20 + ["bob"] * 10 + ["carol"] * 6 + ["dave"] * 4
    surnames = ["smith", "jones", "brown", "davis"] * (n // 4)
    return pl.DataFrame({"first_name": first_names[:n], "surname": surnames[:n]})


def test_tf_tables_lever_builds_table_for_tf_adjustment_field():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    assert conversion.em_model is not None
    assert conversion.em_model.tf_freqs is None

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    assert result.em_model is not None
    freqs = result.em_model.tf_freqs["first_name"]
    assert freqs.keys() == {"alice", "bob", "carol", "dave"}
    assert freqs["alice"] == pytest.approx(20 / 40)
    assert sum(freqs.values()) == pytest.approx(1.0)
    assert result.em_model.tf_collision["first_name"] == pytest.approx(
        sum(f**2 for f in freqs.values())
    )

    # Baseline untouched (copy-on-write).
    assert conversion.em_model.tf_freqs is None


def test_tf_tables_lever_skips_field_without_tf_adjustment():
    conversion = from_splink(_trained_settings_with_tf_adjustment())

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    assert result.em_model.tf_freqs is not None
    assert "surname" not in result.em_model.tf_freqs
    per_field_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and "surname" in f.message
    ]
    assert per_field_findings == []


def test_tf_tables_lever_finding_has_field_name_and_distinct_count():
    conversion = from_splink(_trained_settings_with_tf_adjustment())

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    tf_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and f.severity == "info"
        and "first_name" in f.message
    ]
    assert len(tf_findings) == 1
    assert "4" in tf_findings[0].message  # distinct-value count


def test_tf_tables_lever_skips_field_already_present():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    # Pre-populate the copy scenario: mutate a from_dict-built EMResult fixture
    # so the imported model already carries a TF table for first_name.
    conversion.em_model.tf_freqs = {"first_name": {"alice": 1.0}}
    conversion.em_model.tf_collision = {"first_name": 1.0}

    result = upgrade_splink_conversion(
        conversion, _skewed_tf_df(), levers={"tf_tables"}, measure=False
    )

    # Untouched -- the pre-existing table wins, no rebuild.
    assert result.em_model.tf_freqs["first_name"] == {"alice": 1.0}
    skip_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and "already" in f.message.lower()
    ]
    assert len(skip_findings) == 1
    assert "first_name" in skip_findings[0].message


def test_tf_tables_lever_null_column_warns_and_skips():
    conversion = from_splink(_trained_settings_with_tf_adjustment())
    df = pl.DataFrame(
        {"first_name": [None] * 10, "surname": ["smith"] * 10}
    )

    result = upgrade_splink_conversion(
        conversion, df, levers={"tf_tables"}, measure=False
    )

    assert result.em_model.tf_freqs is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:tf_tables" and f.severity == "warning"
    ]
    assert len(warn_findings) == 1
    assert "first_name" in warn_findings[0].message


# ── Lever 2: measured distance thresholds ────────────────────────────────────


def _field(config, name):
    mk = config.get_matchkeys()[0]
    for f in mk.fields:
        if f.field == name:
            return f
    raise AssertionError(f"field '{name}' not found in matchkey")


def _levenshtein_comparison(column, distances, *, trained=False, bands=None, else_probs=None):
    """A comparison with one levenshtein band per entry in ``distances``
    (ascending order in the input; from_splink sorts the resulting
    thresholds descending). ``trained``/``bands``/``else_probs`` add
    m_probability/u_probability so ``detect_trained`` picks it up --
    ``bands`` is a list of (m, u) parallel to ``distances``."""
    levels = [
        {
            "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL',
            "is_null_level": True,
        },
    ]
    for i, d in enumerate(distances):
        level = {"sql_condition": f'levenshtein("{column}_l", "{column}_r") <= {d}'}
        if trained:
            m, u = bands[i]
            level["m_probability"] = m
            level["u_probability"] = u
        levels.append(level)
    else_level = {"sql_condition": "ELSE"}
    if trained:
        m_else, u_else = else_probs
        else_level["m_probability"] = m_else
        else_level["u_probability"] = u_else
    levels.append(else_level)
    return {"output_column_name": column, "comparison_levels": levels}


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


def _bare_settings_with_levenshtein(distances=(1, 3), column="email"):
    return {
        "comparisons": [
            _levenshtein_comparison(column, distances),
            _exact_only_comparison("surname"),
        ],
        "blocking_rules_to_generate_predictions": [f'l."{column}" = r."{column}"'],
    }


def _trained_settings_with_levenshtein(
    distances=(1, 3),
    bands=((0.5, 0.1), (0.3, 0.2)),
    else_probs=(0.2, 0.7),
    column="email",
    extra_comparison=None,
):
    comparisons = [
        _levenshtein_comparison(
            column, distances, trained=True, bands=bands, else_probs=else_probs
        ),
        _trained_exact_comparison("surname"),
    ]
    if extra_comparison is not None:
        comparisons.append(extra_comparison)
    return {
        "comparisons": comparisons,
        "blocking_rules_to_generate_predictions": [f'l."{column}" = r."{column}"'],
        "probability_two_random_records_match": 0.001,
    }


def _levenshtein_df(mean_len, n=30, column="email", value=None):
    val = value if value is not None else "a" * mean_len
    return pl.DataFrame({column: [val] * n, "surname": ["smith"] * n})


def test_distance_thresholds_lever_recomputes_from_measured_length():
    conversion = from_splink(_trained_settings_with_levenshtein())
    assert conversion.em_model is not None
    field = _field(conversion.config, "email")
    assert field.level_thresholds == pytest.approx([0.9, 0.7])
    assert field.levels == 3

    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20),
        levers={"distance_thresholds"},
        measure=False,
    )

    upgraded_field = _field(result.upgraded_config, "email")
    assert upgraded_field.level_thresholds == pytest.approx([0.95, 0.85])
    assert upgraded_field.levels == 3  # unchanged: no collapse

    findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:distance_thresholds" and "email" in f.message
    ]
    assert any("0.9" in f.message and "0.95" in f.message and "d=1" in f.message for f in findings)
    assert any("0.7" in f.message and "0.85" in f.message and "d=3" in f.message for f in findings)

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    result.em_model.validate_for(upgraded_mk)  # must not raise

    # Baseline copy-on-write: original conversion is untouched.
    assert _field(conversion.config, "email").level_thresholds == pytest.approx([0.9, 0.7])


def test_distance_thresholds_lever_two_level_legacy_shape():
    conversion = from_splink(
        _trained_settings_with_levenshtein(distances=(1,), bands=((0.6, 0.1),), else_probs=(0.4, 0.9))
    )
    field = _field(conversion.config, "email")
    assert field.level_thresholds is None
    assert field.partial_threshold == pytest.approx(0.9)
    assert field.levels == 2

    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20),
        levers={"distance_thresholds"},
        measure=False,
    )

    upgraded_field = _field(result.upgraded_config, "email")
    assert upgraded_field.level_thresholds is None
    assert upgraded_field.partial_threshold == pytest.approx(1 - 1 / 20)
    assert upgraded_field.levels == 2

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    result.em_model.validate_for(upgraded_mk)


def test_distance_thresholds_lever_leaves_jaro_winkler_field_untouched():
    conversion = from_splink(
        _trained_settings_with_levenshtein(extra_comparison=_trained_jw_comparison())
    )

    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20).with_columns(pl.lit("alice").alias("first_name")),
        levers={"distance_thresholds"},
        measure=False,
    )

    jw_field_before = _field(conversion.config, "first_name")
    jw_field_after = _field(result.upgraded_config, "first_name")
    assert jw_field_after.level_thresholds == jw_field_before.level_thresholds
    assert jw_field_after.partial_threshold == jw_field_before.partial_threshold

    findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:distance_thresholds" and "first_name" in f.message
    ]
    assert findings == []


def test_distance_thresholds_lever_collapses_bands_and_sums_em_mass():
    conversion = from_splink(
        _trained_settings_with_levenshtein(
            distances=(1, 3), bands=((0.5, 0.1), (0.3, 0.2)), else_probs=(0.2, 0.7)
        )
    )
    old_m = list(conversion.em_model.m_probs["email"])
    old_u = list(conversion.em_model.u_probs["email"])
    assert len(old_m) == 3  # [else, d=3 band, d=1 band]

    # L=2: d=1 -> max(0, 1-1/2)=0.5 (valid); d=3 -> max(0, 1-3/2)=0.0 (invalid,
    # dropped and merged into the disagree level).
    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=2),
        levers={"distance_thresholds"},
        measure=False,
    )

    upgraded_field = _field(result.upgraded_config, "email")
    assert upgraded_field.levels == 2
    assert upgraded_field.level_thresholds == pytest.approx([0.5])

    new_m = result.em_model.m_probs["email"]
    new_u = result.em_model.u_probs["email"]
    assert len(new_m) == 2
    # level0 (disagree) absorbed the dropped d=3 band's mass; level1 keeps
    # the surviving d=1 band's mass. Values are renormalized (sum to 1) but
    # proportional to old_else+old_d3 : old_d1.
    expected_m0 = old_m[0] + old_m[1]
    expected_m1 = old_m[2]
    total_m = expected_m0 + expected_m1
    assert new_m[0] == pytest.approx(expected_m0 / total_m)
    assert new_m[1] == pytest.approx(expected_m1 / total_m)
    total_u = (old_u[0] + old_u[1]) + old_u[2]
    assert new_u[0] == pytest.approx((old_u[0] + old_u[1]) / total_u)
    assert new_u[1] == pytest.approx(old_u[2] / total_u)

    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:distance_thresholds" and f.severity == "warning"
        and "email" in f.message
    ]
    assert warn_findings
    assert any("merged" in f.message.lower() or "summed" in f.message.lower() for f in warn_findings)

    # Config round-trips (schema still valid after the level-count change).
    from goldenmatch.config.schemas import GoldenMatchConfig
    GoldenMatchConfig(**result.upgraded_config.model_dump())

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    result.em_model.validate_for(upgraded_mk)  # must not raise (2 weights for 2 levels)


def test_distance_thresholds_lever_empty_column_warns_and_skips():
    conversion = from_splink(_trained_settings_with_levenshtein())
    df = pl.DataFrame({"email": [None] * 10, "surname": ["smith"] * 10})

    result = upgrade_splink_conversion(
        conversion, df, levers={"distance_thresholds"}, measure=False
    )

    upgraded_field = _field(result.upgraded_config, "email")
    assert upgraded_field.level_thresholds == pytest.approx([0.9, 0.7])  # unchanged

    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:distance_thresholds" and f.severity == "warning"
        and "email" in f.message
    ]
    assert len(warn_findings) == 1


def test_distance_thresholds_lever_bare_settings_no_em_handling():
    conversion = from_splink(_bare_settings_with_levenshtein())
    assert conversion.em_model is None

    result = upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20),
        levers={"distance_thresholds"},
        measure=False,
    )

    upgraded_field = _field(result.upgraded_config, "email")
    assert upgraded_field.level_thresholds == pytest.approx([0.95, 0.85])
    assert result.em_model is None


def test_distance_thresholds_lever_baseline_thresholds_unchanged():
    conversion = from_splink(_trained_settings_with_levenshtein())
    baseline_dump_before = conversion.config.model_dump()

    upgrade_splink_conversion(
        conversion,
        _levenshtein_df(mean_len=20),
        levers={"distance_thresholds"},
        measure=False,
    )

    assert conversion.config.model_dump() == baseline_dump_before
    assert _field(conversion.config, "email").level_thresholds == pytest.approx([0.9, 0.7])


# ── Lever 3: threshold calibration ───────────────────────────────────────────


def _calibration_settings():
    """Trained settings blocked on surname ONLY (a single static key), so the
    blocked candidate pairs are exactly the within-surname-group pairs --
    tests engineer the pair count via group sizes (a group of n rows yields
    n*(n-1)/2 pairs).

    proportion_matched drives compute_thresholds' percentile indices
    (link at 1 - 2*p, review at 1 - 5*p): 0.05 lands them in the BODY of the
    570-pair fixture distribution. A near-zero p would push both indices
    into the saturated exact-dupe top (normalized weight 1.0) and collapse
    link == review == the 0.95 clamp."""
    return {
        "comparisons": [_trained_jw_comparison(), _trained_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
        "probability_two_random_records_match": 0.05,
    }


def _calibration_block_names():
    """20 first_names with a similarity SPREAD: exact dupes, near-dupes,
    unrelated."""
    return (
        ["alice"] * 5  # exact duplicates
        + ["alicia", "alissa"]  # near-dupes of alice
        + ["bob", "bobb"]  # near pair
        + [
            "carol", "dave", "erin", "frank", "grace", "henry",
            "irene", "jack", "karen", "louis", "xavier",
        ]  # unrelated
    )


def _calibration_df():
    """60 rows in three surname blocks of 20 rows each -- 3 * C(20,2) = 570
    blocked candidate pairs (well over 50) with a spread of first_name
    similarities inside every block."""
    fn: list[str] = []
    sn: list[str] = []
    for s in ("smith", "jones", "brown"):
        fn.extend(_calibration_block_names())
        sn.extend([s] * 20)
    return pl.DataFrame({"first_name": fn, "surname": sn})


def test_calibration_lever_sets_thresholds_and_finding():
    conversion = from_splink(_calibration_settings())
    assert conversion.em_model is not None
    baseline_mk = conversion.config.get_matchkeys()[0]
    assert baseline_mk.link_threshold is None
    assert baseline_mk.review_threshold is None

    result = upgrade_splink_conversion(
        conversion, _calibration_df(), levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert isinstance(upgraded_mk.link_threshold, float)
    assert isinstance(upgraded_mk.review_threshold, float)
    assert 0.0 < upgraded_mk.link_threshold < 1.0
    assert 0.0 < upgraded_mk.review_threshold < 1.0
    assert upgraded_mk.review_threshold < upgraded_mk.link_threshold

    # Baseline stays uncalibrated (copy-on-write).
    assert conversion.config.get_matchkeys()[0].link_threshold is None
    assert conversion.config.get_matchkeys()[0].review_threshold is None

    findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "info"
    ]
    assert len(findings) == 1
    msg = findings[0].message
    assert str(upgraded_mk.link_threshold) in msg
    assert str(upgraded_mk.review_threshold) in msg
    assert "570" in msg  # n blocked candidate pairs


def test_calibration_lever_exactly_50_pairs_skips():
    """compute_thresholds' data-driven branch requires len STRICTLY > 50; at
    exactly 50 it silently falls through to fixed defaults, which must never
    be presented as calibrated -- the lever skips at <= 50."""
    conversion = from_splink(_calibration_settings())

    # Block sizes 5 + 9 + 3 + 2 -> 10 + 36 + 3 + 1 = exactly 50 pairs;
    # unique-surname singletons contribute nothing.
    sizes = {"smith": 5, "jones": 9, "brown": 3, "davis": 2, "solo1": 1, "solo2": 1}
    fn: list[str] = []
    sn: list[str] = []
    i = 0
    for surname, n in sizes.items():
        for _ in range(n):
            fn.append(f"name{i}")
            sn.append(surname)
            i += 1
    df = pl.DataFrame({"first_name": fn, "surname": sn})

    result = upgrade_splink_conversion(
        conversion, df, levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold is None
    assert upgraded_mk.review_threshold is None

    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "warning"
    ]
    assert len(warn_findings) == 1
    assert "50" in warn_findings[0].message
    assert "skip" in warn_findings[0].message.lower()


def test_calibration_lever_tiny_df_skips():
    conversion = from_splink(_calibration_settings())
    df = pl.DataFrame(
        {
            "first_name": ["alice", "alice", "bob", "bob"],
            "surname": ["smith", "smith", "jones", "jones"],
        }
    )  # two blocks of 2 -> 2 blocked pairs

    result = upgrade_splink_conversion(
        conversion, df, levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold is None
    assert upgraded_mk.review_threshold is None
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "warning"
    ]
    assert len(warn_findings) == 1


def test_calibration_lever_posterior_mode_skips(monkeypatch):
    """Posterior scoring mode: compute_thresholds returns fixed absolute cuts
    (0.99/0.50) by design and ignores the distribution -- the lever detects
    the mode (read from GOLDENMATCH_FS_CALIBRATED by _fs_calibration_mode)
    and skips with an info note, before any pair work."""
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    conversion = from_splink(_calibration_settings())

    result = upgrade_splink_conversion(
        conversion, _calibration_df(), levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold is None
    assert upgraded_mk.review_threshold is None
    info_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "info"
    ]
    assert len(info_findings) == 1
    assert "posterior" in info_findings[0].message.lower()


def test_calibration_lever_partial_model_skips():
    """Mixed bare/trained settings import a PARTIAL model (import_em skips
    the bare comparison with a warning), so em.match_weights does not cover
    every matchkey field. The calibration lever must warn + skip -- levers
    never fail the pass -- not crash with a raw KeyError scoring candidate
    pairs."""
    settings = {
        "comparisons": [_trained_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
        "probability_two_random_records_match": 0.05,
    }
    conversion = from_splink(settings)
    assert conversion.em_model is not None
    assert "surname" not in conversion.em_model.match_weights

    # _calibration_df yields 570 blocked pairs -- calibration WOULD run if
    # the partial-coverage guard didn't skip first.
    result = upgrade_splink_conversion(
        conversion, _calibration_df(), levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold is None
    assert upgraded_mk.review_threshold is None

    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "warning"
    ]
    assert len(warn_findings) == 1
    assert "partial" in warn_findings[0].message
    assert "surname" in warn_findings[0].message


def _ne_bearing_conversion(*, w_fired=-3.0):
    """A trained conversion in the F3 fan_out-lever output shape: the
    matchkey carries an EM-learned ``NegativeEvidenceField`` on ``phone``
    plus the matching ``__ne__phone`` model entries (``match_weights``
    stored as ``[w_fired, 0.0]``)."""
    from goldenmatch.config.schemas import NegativeEvidenceField

    conversion = from_splink(_calibration_settings())
    assert conversion.em_model is not None
    conversion.config.get_matchkeys()[0].negative_evidence = [
        NegativeEvidenceField(field="phone", scorer="exact", threshold=0.95),
    ]
    key = "__ne__phone"
    conversion.em_model.m_probs[key] = [0.0625, 0.9375]
    conversion.em_model.u_probs[key] = [0.5, 0.5]
    conversion.em_model.match_weights[key] = [w_fired, 0.0]
    return conversion


def _calibration_df_with_phone(values=None):
    """``_calibration_df`` plus a ``phone`` column (all-null by default)."""
    df = _calibration_df()
    if values is None:
        values = [None] * len(df)
    return df.with_columns(pl.Series("phone", values, dtype=pl.Utf8))


def test_calibration_runs_on_ne_bearing_config():
    """The NE tripwire is GONE (Task F5): an NE-bearing matchkey (the F3
    fan_out output shape -- NE field + ``__ne__`` model entries) calibrates
    thresholds instead of warn+skipping. The per-pair weight sum includes
    NE contributions (``_ne_scalar_contribution``) and the normalization
    range comes from ``fs_weight_range``, so the calibrated cuts live on
    the same scale runtime scoring uses for NE-bearing configs."""
    conversion = _ne_bearing_conversion()
    # Distinct phones within every block -> the NE FIRES on blocked pairs.
    phones = [f"555-{i:07d}" for i in range(60)]
    result = upgrade_splink_conversion(
        conversion,
        _calibration_df_with_phone(phones),
        levers={"calibration"},
        measure=False,
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert isinstance(upgraded_mk.link_threshold, float)
    assert isinstance(upgraded_mk.review_threshold, float)
    assert 0.0 < upgraded_mk.link_threshold <= 1.0
    assert 0.0 < upgraded_mk.review_threshold <= 1.0

    # No warn finding about negative evidence -- the tripwire is gone.
    warn_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "warning"
    ]
    assert warn_findings == []
    info_findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "info"
    ]
    assert len(info_findings) == 1
    assert "570" in info_findings[0].message  # n blocked candidate pairs

    # Mutation square: the NE contributions genuinely MOVE the calibrated
    # cuts. An implementation that silently dropped NE from the per-pair
    # weight sums / normalization range would still produce in-range floats
    # above -- but it would land exactly where the plain (no-NE) conversion
    # lands on the same data, and this assert would catch it.
    plain_result = upgrade_splink_conversion(
        from_splink(_calibration_settings()),
        _calibration_df_with_phone(phones),
        levers={"calibration"},
        measure=False,
    )
    plain_mk = plain_result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold != plain_mk.link_threshold


def test_calibration_ne_parity_when_never_fires():
    """A zero-contribution NE (``__ne__`` weight range [0.0, 0.0]) on an
    all-null NE column (never fires) must leave calibration EXACTLY where
    an otherwise-identical no-NE run lands: fs_weight_range adds (0, 0) to
    the model range and _ne_scalar_contribution adds 0.0 to every pair."""
    df = _calibration_df_with_phone()  # phone all-null: NE never fires

    ne_result = upgrade_splink_conversion(
        _ne_bearing_conversion(w_fired=0.0), df,
        levers={"calibration"}, measure=False,
    )
    plain_result = upgrade_splink_conversion(
        from_splink(_calibration_settings()), df,
        levers={"calibration"}, measure=False,
    )

    ne_mk = ne_result.upgraded_config.get_matchkeys()[0]
    plain_mk = plain_result.upgraded_config.get_matchkeys()[0]
    assert ne_mk.link_threshold is not None
    assert ne_mk.link_threshold == plain_mk.link_threshold
    assert ne_mk.review_threshold == plain_mk.review_threshold


def test_calibration_uses_fs_weight_range(monkeypatch):
    """The hand-rolled min/max weight sums are gone: calibration reaches
    the shared ``fs_weight_range`` (core/probabilistic) for its
    normalization range. Monkeypatched to raise a sentinel -> the lever
    hits it."""
    import goldenmatch.core.probabilistic as prob

    class _Sentinel(Exception):
        pass

    def _boom(em, mk):
        raise _Sentinel("fs_weight_range reached")

    monkeypatch.setattr(prob, "fs_weight_range", _boom)

    conversion = from_splink(_calibration_settings())
    with pytest.raises(_Sentinel):
        upgrade_splink_conversion(
            conversion, _calibration_df(), levers={"calibration"}, measure=False
        )


def test_calibration_lever_reestimates_within_block_rate_from_tiny_prior():
    """An imported Splink model carries probability_two_random_records_match
    -- a RANDOM-PAIR prior -- in proportion_matched, not the within-block
    match rate compute_thresholds' percentile math expects (GM's own
    train_em estimates lambda ON blocked pairs). Trusting the raw prior on
    match-dense blocks cuts at the extreme top of the blocked-pair
    distribution (link -> the 0.95 clamp) and collapses recall. The lever
    must re-estimate the within-block rate from the model's own likelihood
    ratios (mean pair posterior under an equal-odds prior) before computing
    thresholds -- and must NOT mutate the shipped model's
    proportion_matched while doing so."""
    settings = {
        "comparisons": [_trained_jw_comparison(), _trained_exact_comparison("surname")],
        "blocking_rules_to_generate_predictions": ['l."surname" = r."surname"'],
        "probability_two_random_records_match": 0.001,
    }
    conversion = from_splink(settings)

    # 6 surname blocks of 5 IDENTICAL first_names (6 * C(5,2) = 60 exact-dupe
    # pairs) + one block of 11 unrelated names (C(11,2) = 55 non-match pairs)
    # = 115 blocked pairs, true within-block match rate ~0.52.
    fn: list[str] = []
    sn: list[str] = []
    for i, s in enumerate(["smith", "jones", "brown", "davis", "clark", "lewis"]):
        fn.extend([f"dupe{i}"] * 5)
        sn.extend([s] * 5)
    unrelated = [
        "carol", "dave", "erin", "frank", "grace", "henry",
        "irene", "jack", "karen", "louis", "xavier",
    ]
    fn.extend(unrelated)
    sn.extend(["walker"] * len(unrelated))
    df = pl.DataFrame({"first_name": fn, "surname": sn})

    result = upgrade_splink_conversion(
        conversion, df, levers={"calibration"}, measure=False
    )

    upgraded_mk = result.upgraded_config.get_matchkeys()[0]
    assert upgraded_mk.link_threshold is not None
    # Raw 0.001 prior -> link_idx at the 99.8th percentile of 115 pairs, deep
    # in the saturated exact-dupe top (normalized 1.0, clamped to 0.95). The
    # E-step estimate (~0.5) pulls the cut down into the distribution body.
    assert upgraded_mk.link_threshold <= 0.5
    # Copy-on-write: the shipped upgraded model keeps the imported prior.
    assert result.em_model is not None
    assert result.em_model.proportion_matched == pytest.approx(0.001)
    assert conversion.em_model is not None
    assert conversion.em_model.proportion_matched == pytest.approx(0.001)

    findings = [
        f for f in result.report.findings
        if f.splink_path == "upgrade:calibration" and f.severity == "info"
    ]
    assert len(findings) == 1
    assert "within-block" in findings[0].message


def test_lever_order_canonical():
    """The finding-emitting levers' first occurrences must appear in canonical
    registry order. (fan_out sits between distance_thresholds and calibration
    but its stub bodies emit no finding on trained input, so it is asserted
    via _LEVER_ORDER only.)"""
    from goldenmatch.config.splink_upgrade import _LEVER_ORDER

    assert _LEVER_ORDER == (
        "tf_tables",
        "distance_thresholds",
        "fan_out",
        "calibration",
    )

    conversion = from_splink(_trained_settings_with_levenshtein())
    # 15 identical emails -> one block of 15 -> 105 blocked pairs (> 50), so
    # the calibration lever runs for real after the distance lever adjusts
    # the model.
    df = _levenshtein_df(mean_len=20, n=15)

    result = upgrade_splink_conversion(conversion, df, measure=False)

    paths = [f.splink_path for f in result.report.findings]
    first = {
        name: paths.index(f"upgrade:{name}")
        for name in ("tf_tables", "distance_thresholds", "calibration")
    }
    assert first["tf_tables"] < first["distance_thresholds"] < first["calibration"]


# ── Unknown lever name ────────────────────────────────────────────────────────


def test_unknown_lever_name_raises():
    conversion = from_splink(_bare_settings())

    with pytest.raises(SplinkUpgradeError):
        upgrade_splink_conversion(
            conversion, _sample_df(), levers={"nope"}, measure=False
        )


# ── measure wiring (U5) ───────────────────────────────────────────────────────


def test_measure_true_runs_measurement():
    """Bare-settings + measure=True: measurement runs (EM trains on the
    sample at run time). Deep coverage lives in test_splink_upgrade_measure."""
    from goldenmatch.config.splink_upgrade import MeasurementResult

    conversion = from_splink(_bare_settings())

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers=set(), measure=True
    )

    assert isinstance(result.measurement, MeasurementResult)


def test_measure_false_records_skip_note():
    conversion = from_splink(_bare_settings())

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers=set(), measure=False
    )

    assert result.measurement is None
    assert any(
        f.splink_path == "upgrade:measure" and "skipped" in f.message
        for f in result.report.findings
    )
