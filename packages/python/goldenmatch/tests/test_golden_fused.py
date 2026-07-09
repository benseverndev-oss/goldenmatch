"""Parity + gate tests for the fused Arrow-native golden-record kernel.

Every parity test forces the reference (`build_golden_records_batch`) OFF the
approximating polars-native fast path (via an explicit `field_rules` entry) onto
the exact `merge_field` survivorship path, which is the byte-parity oracle. See
`docs/superpowers/plans/2026-07-08-fused-golden-record-kernel.md` (Conventions).
"""

from __future__ import annotations

import polars as pl

from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch
from goldenmatch.core.golden_fused import golden_fused_ready, run_golden_fused_arrow


# ─── Gate tests (Task 0.1) ───────────────────────────────────────────────────


def test_gate_accepts_simple_default_strategy():
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert golden_fused_ready(rules) is True


def test_gate_accepts_covered_field_rule():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="majority_vote")},
    )
    assert golden_fused_ready(rules) is True


def test_gate_declines_validator():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        # validate_with populates via its "validate" alias.
        field_rules={"phone": GoldenFieldRule(strategy="most_complete", validate="phone")},
    )
    assert golden_fused_ready(rules) is False


def test_gate_declines_custom_plugin():
    # A custom (plugin-backed) strategy is only constructable on a GoldenFieldRule
    # (the top-level default_strategy validator rejects non-standard names); the
    # gate must decline it since the kernel has no plugin dispatch.
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="custom:my_plugin")},
    )
    assert golden_fused_ready(rules) is False


def test_gate_declines_llm():
    rules = GoldenRulesConfig(default_strategy="most_complete", use_llm_for_ambiguous=True)
    assert golden_fused_ready(rules) is False


# ─── most_complete end-to-end parity (Task 0.3) ──────────────────────────────


def _cluster_frame():
    # two clusters, within-cluster __row_id__ ascending (spec 4.3)
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "name": ["Bob", "Robert", "Bob", "Sue", "Suzanne"],
        }
    )


def test_run_declines_fast_path_eligible_simple_config():
    df = _cluster_frame()
    # simple most_complete default, no field_rules/groups/overrides, no quality_scores
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert run_golden_fused_arrow(df, rules) is None  # routes to fast columnar path


def test_most_complete_matches_reference():
    df = _cluster_frame()
    # EXPLICIT field_rule forces the reference off the approximating fast columnar
    # path onto the exact merge_field path (see the oracle note in Conventions).
    # A bare default_strategy="most_complete" would route to the fast path and is
    # DECLINED by run_golden_fused_arrow (returns None).
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    ref = build_golden_records_batch(df, rules)  # list[dict]
    got = run_golden_fused_arrow(df, rules)  # pl.DataFrame
    assert got is not None
    ref_map = {r["__cluster_id__"]: r for r in ref}
    for row in got.iter_rows(named=True):
        cid = row["__cluster_id__"]
        assert row["name"] == ref_map[cid]["name"]["value"]
        assert abs(row["__golden_confidence__"] - ref_map[cid]["__golden_confidence__"]) < 1e-12
