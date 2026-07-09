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
from goldenmatch.core.golden_fused import (
    _factorize_codes,
    golden_fused_ready,
    run_golden_fused_arrow,
)

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


# ─── factorization helper (Task 1.1) ─────────────────────────────────────────


def test_factorize_respects_python_equality_and_order():
    # int 1 and float 1.0 are == in Python -> same code; None -> -1; codes
    # assigned in first-occurrence order.
    vals = [1, 1.0, None, "x", 1]
    codes = _factorize_codes(vals)
    assert codes == [0, 0, -1, 1, 0]


def test_factorize_empty_and_all_null():
    assert _factorize_codes([]) == []
    assert _factorize_codes([None, None]) == [-1, -1]


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


def test_run_drops_singleton_clusters():
    # cluster 2 is a singleton; the fused path filters it itself (size > 1), but
    # the oracle build_golden_records_batch does NOT self-filter. Per the plan's
    # "harness asymmetry" note, feed the reference the PRE-FILTERED frame and the
    # fused path the RAW frame, then assert equal output on the surviving clusters.
    raw = pl.DataFrame(
        {
            "__row_id__": [0, 1, 5, 10, 11],
            "__cluster_id__": [1, 1, 2, 3, 3],
            "name": ["Bob", "Robert", "Solo", "Sue", "Suzanne"],
        }
    )
    pre_filtered = raw.filter(pl.col("__cluster_id__") != 2)  # drop the singleton
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    ref = build_golden_records_batch(pre_filtered, rules)
    got = run_golden_fused_arrow(raw, rules)
    assert got is not None

    got_cids = set(got.get_column("__cluster_id__").to_list())
    assert got_cids == {1, 3}  # singleton cluster 2 dropped
    ref_map = {r["__cluster_id__"]: r for r in ref}
    assert set(ref_map) == {1, 3}
    for row in got.iter_rows(named=True):
        cid = row["__cluster_id__"]
        assert row["name"] == ref_map[cid]["name"]["value"]
        assert abs(row["__golden_confidence__"] - ref_map[cid]["__golden_confidence__"]) < 1e-12


def test_most_complete_tie_null_and_multicolumn():
    # Exercises, through the Python round-trip: (a) a most_complete length TIE
    # (-> conf 0.7), (b) an all-null column so the kernel's -1 sentinel round-trips
    # through _gather_with_nulls to a real null, and (c) TWO user columns so the
    # mean-confidence path (n_cols > 1) runs.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            # cluster 1: length tie (all len 2) -> first-in-order "aa", conf 0.7
            # cluster 2: "Suzanne" unique longest -> conf 1.0
            "name": ["aa", "bb", "aa", "Sue", "Suzanne"],
            # cluster 1: entirely null -> value None, conf 0.0 (sentinel path)
            # cluster 2: "zz" unique longest -> conf 1.0
            "extra": [None, None, None, "z", "zz"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(strategy="most_complete"),
            "extra": GoldenFieldRule(strategy="most_complete"),
        },
    )
    ref = build_golden_records_batch(df, rules)
    got = run_golden_fused_arrow(df, rules)
    assert got is not None

    ref_map = {r["__cluster_id__"]: r for r in ref}
    got_map = {row["__cluster_id__"]: row for row in got.iter_rows(named=True)}
    # sanity: the fixture actually hit the branches we intend to cover.
    assert got_map[1]["name"] == "aa" and got_map[1]["extra"] is None
    assert abs(got_map[1]["__golden_confidence__"] - 0.35) < 1e-12  # (0.7 + 0.0)/2
    for cid, row in got_map.items():
        r = ref_map[cid]
        assert row["name"] == r["name"]["value"]
        assert row["extra"] == r["extra"]["value"]
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12


# ─── remaining pure-scalar strategies (Task 1.2) ─────────────────────────────


def _assert_value_conf_parity(df: pl.DataFrame, rules: GoldenRulesConfig, cols: list[str]):
    """Run both paths on the identical frame + config and assert per-cluster
    value + confidence equality on every requested user column."""
    ref = build_golden_records_batch(df, rules)
    got = run_golden_fused_arrow(df, rules)
    assert got is not None
    ref_map = {r["__cluster_id__"]: r for r in ref}
    got_map = {row["__cluster_id__"]: row for row in got.iter_rows(named=True)}
    assert set(got_map) == set(ref_map)
    for cid, row in got_map.items():
        r = ref_map[cid]
        for c in cols:
            assert row[c] == r[c]["value"], f"cluster {cid} col {c}"
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12
    return got_map


def test_majority_vote_matches_reference():
    # cluster 1: count tie a/b (2 each) -> winner = first-appearance "a", conf 0.5
    # cluster 2: clear majority x (2/3), conf 2/3
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 1, 2, 2, 2],
            "v": ["a", "b", "a", "b", "x", "x", "y"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="majority_vote",
        field_rules={"v": GoldenFieldRule(strategy="majority_vote")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    # sanity: the tie resolved to the first-appearance value at conf 0.5.
    assert got[1]["v"] == "a"
    assert abs(got[1]["__golden_confidence__"] - 0.5) < 1e-12


def test_unanimous_or_null_matches_reference():
    # cluster 1: disagreement -> emits null, conf 0.0
    # cluster 2: unanimous non-null (a real null ignored) -> value, conf 1.0
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11, 12],
            "__cluster_id__": [1, 1, 2, 2, 2],
            "v": ["a", "b", "z", None, "z"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="unanimous_or_null",
        field_rules={"v": GoldenFieldRule(strategy="unanimous_or_null")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] is None
    assert abs(got[1]["__golden_confidence__"] - 0.0) < 1e-12
    assert got[2]["v"] == "z"


def test_first_non_null_matches_reference():
    # cluster 1: leading null -> first non-null "b", conf 0.6
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "v": [None, "b", "c", "p", "q"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="first_non_null",
        field_rules={"v": GoldenFieldRule(strategy="first_non_null")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] == "b"
    assert abs(got[1]["__golden_confidence__"] - 0.6) < 1e-12


def test_longest_value_matches_reference():
    # cluster 1: length tie (aa/bb, len 2) -> first-in-order "aa", conf 0.5
    # cluster 2: unique longest "zzz" -> conf 1.0
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "v": ["aa", "bb", "c", "z", "zzz"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="longest_value",
        field_rules={"v": GoldenFieldRule(strategy="longest_value")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] == "aa"
    assert abs(got[1]["__golden_confidence__"] - 0.5) < 1e-12
    assert got[2]["v"] == "zzz"


def test_mixed_type_short_circuit_uses_raw_value_equality():
    # Object column mixing int 1 and float 1.0: they are == (and hash-equal) in
    # Python, so the reference's raw-value short-circuit (set(v) len 1) fires and
    # returns the FIRST value (int 1) at conf 1.0. A text-based short-circuit
    # ("1" != "1.0") would instead run most_complete and return 1.0 -- the bug
    # the code-factorization short-circuit fixes.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "v": pl.Series("v", [1, 1.0], dtype=pl.Object),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"v": GoldenFieldRule(strategy="most_complete")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] == 1
    assert abs(got[1]["__golden_confidence__"] - 1.0) < 1e-12


def test_null_winner_round_trips_on_object_dtype():
    # unanimous_or_null on an Object-dtype column with a DISAGREEMENT emits a
    # null winner (kernel -1 sentinel). This exercises the when/then null path in
    # _gather_with_nulls on an Object column -- pins that it round-trips to a real
    # null matching build_golden_records_batch (a later all-null stage would
    # otherwise silently trip on this).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "v": pl.Series("v", [1, 2], dtype=pl.Object),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="unanimous_or_null",
        field_rules={"v": GoldenFieldRule(strategy="unanimous_or_null")},
    )
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] is None
    assert abs(got[1]["__golden_confidence__"] - 0.0) < 1e-12


# ─── source_priority (Task 2.1) ──────────────────────────────────────────────


def test_source_priority_matches_reference():
    # cluster 1: sources [crm, web]; distinct values -> priority [web, crm]
    #   picks the web row's value at conf 1.0 (idx 0).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "__source__": ["crm", "web", "crm", "web"],
            "name": ["Bob", "Bobby", "Sue", "Susan"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(strategy="source_priority", source_priority=["web", "crm"]),
        },
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] == "Bobby"  # the web row
    assert abs(got[1]["__golden_confidence__"] - 1.0) < 1e-12


def test_source_priority_top_source_null_falls_through():
    # top-priority source `web`'s FIRST row has a null value, so the next
    # priority `crm` wins (idx 1 -> conf 0.9). Distinct non-null values so the
    # universal short-circuit doesn't fire.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "__source__": ["crm", "web", "erp"],
            "name": ["Bob", None, "Robert"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(
                strategy="source_priority", source_priority=["web", "crm", "erp"]
            ),
        },
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] == "Bob"  # crm, since web's first row is null
    assert abs(got[1]["__golden_confidence__"] - 0.9) < 1e-12


def test_source_priority_absent_source_in_list():
    # priority lists a source (`mdm`) ABSENT from the column -> skipped; `crm`
    # wins at idx 1 -> conf 0.9.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "__source__": ["crm", "web"],
            "name": ["Bob", "Bobby"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(
                strategy="source_priority", source_priority=["mdm", "crm", "web"]
            ),
        },
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] == "Bob"  # crm
    assert abs(got[1]["__golden_confidence__"] - 0.9) < 1e-12


def test_source_priority_no_match_emits_null():
    # priority lists only absent sources -> no winner -> null, conf 0.0.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "__source__": ["crm", "web"],
            "name": ["Bob", "Bobby"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(strategy="source_priority", source_priority=["mdm", "erp"]),
        },
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] is None
    assert abs(got[1]["__golden_confidence__"] - 0.0) < 1e-12


# ─── most_recent (Task 2.2) ──────────────────────────────────────────────────


def test_most_recent_matches_reference():
    import datetime as _dt

    # cluster 1: distinct dates -> latest wins, conf 1.0.
    # cluster 2: two rows share the top date -> conf 0.5, first-occurrence wins.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "name": ["Bob", "Robert", "Bobby", "Sue", "Susan"],
            "dt": [
                _dt.date(2020, 1, 1),
                _dt.date(2022, 6, 15),
                _dt.date(2021, 3, 3),
                _dt.date(2023, 5, 5),
                _dt.date(2023, 5, 5),  # tie on top date with row 10
            ],
        },
        schema_overrides={"dt": pl.Date},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    # NOTE: `dt` is also a (non-internal) user column resolved via the default
    # most_complete, so __golden_confidence__ is a mean over {name, dt}; assert
    # the per-field name confidence against the reference to pin the most_recent
    # semantics (unique top date -> 1.0; date tie -> 0.5).
    ref = {r["__cluster_id__"]: r for r in build_golden_records_batch(df, rules)}
    assert got[1]["name"] == "Robert"  # 2022 is latest
    assert abs(ref[1]["name"]["confidence"] - 1.0) < 1e-12
    assert got[2]["name"] == "Sue"  # tie -> first occurrence
    assert abs(ref[2]["name"]["confidence"] - 0.5) < 1e-12


def test_most_recent_null_date_and_null_value_dropped():
    import datetime as _dt

    # row 0: null DATE -> dropped. row 1: null VALUE -> dropped. row 2: eligible.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "name": ["Newest", None, "Kept"],
            "dt": [None, _dt.date(2022, 1, 1), _dt.date(2019, 1, 1)],
        },
        schema_overrides={"dt": pl.Date},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    ref = {r["__cluster_id__"]: r for r in build_golden_records_batch(df, rules)}
    assert got[1]["name"] == "Kept"
    # single eligible row -> name confidence 1.0 (dt column shifts the mean).
    assert abs(ref[1]["name"]["confidence"] - 1.0) < 1e-12


def test_most_recent_all_dates_null_emits_null():
    # every row's date is null -> no eligible row -> null, conf 0.0. Distinct
    # values so the universal short-circuit doesn't fire.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "name": ["Bob", "Robert"],
            "dt": pl.Series("dt", [None, None], dtype=pl.Date),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] is None
    assert abs(got[1]["__golden_confidence__"] - 0.0) < 1e-12
