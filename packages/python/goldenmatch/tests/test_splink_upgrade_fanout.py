"""Tests for the fan_out upgrade lever -- Task F1 scaffold: registry wiring,
lever ordering, bare-settings skip, the shared within-block prior helper, and
reference-input context threading. The lever BODIES (NE suggestion, guard
tuning) are no-op stubs until Tasks F3-F4.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.from_splink import from_splink
from goldenmatch.config.splink_upgrade import (
    SplinkUpgradeError,
    _estimate_within_block_prior,
    _resolve_levers,
    upgrade_splink_conversion,
)

# ── Fixtures (mirror tests/test_splink_upgrade_levers.py bare-settings) ──────


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


def _bare_settings():
    return {
        "comparisons": [_jw_comparison(), _exact_only_comparison("surname")],
        "blocking_rules_to_generate_predictions": [
            'l."first_name" = r."first_name"',
            'l."surname" = r."surname"',
        ],
    }


def _sample_df(n=30):
    first_names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    surnames = ["smith", "jones", "brown", "davis", "wilson", "moore"]
    return pl.DataFrame(
        {
            "rec_id": list(range(n)),
            "first_name": [first_names[i % len(first_names)] for i in range(n)],
            "surname": [surnames[i % len(surnames)] for i in range(n)],
        }
    )


# ── Registry wiring / lever order ─────────────────────────────────────────────


def test_fan_out_in_default_lever_order():
    assert _resolve_levers(None) == [
        "tf_tables",
        "distance_thresholds",
        "fan_out",
        "calibration",
    ]


def test_fan_out_selectable_alone():
    assert _resolve_levers({"fan_out"}) == ["fan_out"]

    with pytest.raises(SplinkUpgradeError):
        _resolve_levers({"fan_out", "nope"})


# ── Bare-settings skip ────────────────────────────────────────────────────────


def test_fan_out_bare_settings_skip():
    conversion = from_splink(_bare_settings())
    baseline_dump = conversion.config.model_dump()

    result = upgrade_splink_conversion(
        conversion, _sample_df(), levers={"fan_out"}, measure=False
    )

    findings = [
        f for f in result.report.findings if f.splink_path == "upgrade:fan_out"
    ]
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "no imported model" in findings[0].message
    # A skipped lever must leave the config untouched.
    assert result.upgraded_config.model_dump() == baseline_dump


# ── Shared within-block prior helper ─────────────────────────────────────────


def test_estimate_within_block_prior():
    # 2^0 / (1 + 2^0) == 0.5 exactly.
    assert _estimate_within_block_prior([0.0]) == pytest.approx(0.5)

    # A strongly negative weight (~0) and a strongly positive one (~1)
    # average to ~0.5.
    assert _estimate_within_block_prior([-20.0, 20.0]) == pytest.approx(
        0.5, abs=1e-5
    )

    with pytest.raises(ValueError):
        _estimate_within_block_prior([])


# ── Context threading of reference inputs ────────────────────────────────────


def test_lever_context_carries_reference_inputs(monkeypatch):
    from goldenmatch.config import splink_upgrade

    conversion = from_splink(_bare_settings())
    clusters_df = pl.DataFrame({"rec_id": [0, 1], "cluster_id": [0, 0]})
    labels_df = pl.DataFrame({"rec_id": [0, 1], "cluster_id": [0, 1]})

    seen = {}

    def _spy_fan_out(ctx):
        seen["splink_clusters"] = ctx.splink_clusters
        seen["labels"] = ctx.labels
        seen["id_column"] = ctx.id_column

    monkeypatch.setitem(splink_upgrade._LEVER_REGISTRY, "fan_out", _spy_fan_out)

    upgrade_splink_conversion(
        conversion,
        _sample_df(),
        splink_clusters=clusters_df,
        labels=labels_df,
        id_column="rec_id",
        levers={"fan_out"},
        measure=False,
    )

    assert seen["splink_clusters"] is clusters_df
    assert seen["labels"] is labels_df
    assert seen["id_column"] == "rec_id"
