"""Parity + gate tests for the fused Arrow-native golden-record kernel.

Every parity test forces the reference (`build_golden_records_batch`) OFF the
approximating polars-native fast path (via an explicit `field_rules` entry) onto
the exact `merge_field` survivorship path, which is the byte-parity oracle. See
`docs/superpowers/plans/2026-07-08-fused-golden-record-kernel.md` (Conventions).
"""

from __future__ import annotations

import datetime as _dt

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig
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


# ─── most_recent date-dtype gate (the _MOST_RECENT_ORDER_SAFE_DTYPES allow-list)


def test_most_recent_uint64_date_declines():
    # UInt64's cast to Int64 wraps for values >= 2**63, so the fused path can't
    # guarantee ordering parity -> declines (returns None), caller falls back.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "name": ["Bob", "Robert"],
            "dt": pl.Series("dt", [1, 2], dtype=pl.UInt64),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    assert run_golden_fused_arrow(df, rules) is None


def test_most_recent_string_date_declines():
    # String dates order lexically in Python; no order-preserving i64 physical
    # repr -> declines rather than risk divergence from the reference.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "name": ["Bob", "Robert"],
            "dt": ["2020-01-01", "2022-06-15"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    assert run_golden_fused_arrow(df, rules) is None


def test_most_recent_datetime_matches_reference():
    import datetime as _dt

    # pl.Datetime physical is i64 microseconds -> order-preserving. Latest wins.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "name": ["Bob", "Robert", "Bobby"],
            "dt": [
                _dt.datetime(2020, 1, 1, 8, 0, 0),
                _dt.datetime(2022, 6, 15, 9, 30, 0),
                _dt.datetime(2021, 3, 3, 12, 0, 0),
            ],
        },
        schema_overrides={"dt": pl.Datetime},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] == "Robert"  # 2022 latest


def test_most_recent_integer_date_matches_reference():
    # An integer date column (e.g. an epoch-day / version counter) has an
    # identity i64 physical -> order-preserving; exercises the Int* branch.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "name": ["Bob", "Robert", "Bobby"],
            "dt": pl.Series("dt", [100, 305, 202], dtype=pl.Int64),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got = _assert_value_conf_parity(df, rules, ["name"])
    assert got[1]["name"] == "Robert"  # dt 305 is the max


# ─── quality-weight tie-breaks (Task 3.1) ────────────────────────────────────


def _assert_value_conf_parity_q(df, rules, cols, quality_scores):
    """Like ``_assert_value_conf_parity`` but threads ``quality_scores`` into BOTH
    paths. A non-None ``quality_scores`` forces the reference off the fast columnar
    path (``_polars_native_eligible`` returns False), so the fused path does not
    decline on the fast-path gate either -- both run the exact weighted oracle."""
    ref = build_golden_records_batch(df, rules, quality_scores=quality_scores)
    got = run_golden_fused_arrow(df, rules, quality_scores=quality_scores)
    assert got is not None
    ref_map = {r["__cluster_id__"]: r for r in ref}
    got_map = {row["__cluster_id__"]: row for row in got.iter_rows(named=True)}
    assert set(got_map) == set(ref_map)
    for cid, row in got_map.items():
        r = ref_map[cid]
        for c in cols:
            assert row[c] == r[c]["value"], f"cluster {cid} col {c}"
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12
    return got_map, ref_map


def test_quality_scores_forces_off_fast_path():
    # A bare most_complete default declines (fast-path eligible); adding a non-None
    # quality_scores makes it exact-path-eligible, so the fused path RUNS (does not
    # decline) even without a field_rule -- confirms the _polars_native_eligible
    # reuse with quality_scores in scope.
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "__cluster_id__": [1, 1], "name": ["aa", "bb"]}
    )
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert run_golden_fused_arrow(df, rules) is None  # no quality_scores -> fast path
    qs = {(0, "name"): 0.5, (1, "name"): 0.9}
    assert run_golden_fused_arrow(df, rules, quality_scores=qs) is not None


def test_most_complete_weighted_tie_matches_reference():
    # Length tie "aa"/"bb"; weights 0.5 vs 0.9 -> the higher-weight row (row 1,
    # "bb") wins at conf min(1.0, 0.7*0.9) = 0.63.
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "__cluster_id__": [1, 1], "name": ["aa", "bb"]}
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    qs = {(0, "name"): 0.5, (1, "name"): 0.9}
    got, _ = _assert_value_conf_parity_q(df, rules, ["name"], qs)
    assert got[1]["name"] == "bb"
    assert abs(got[1]["__golden_confidence__"] - min(1.0, 0.7 * 0.9)) < 1e-12


def test_longest_value_weighted_tie_conf_is_flat_07():
    # THE DIVERGENCE: identical fixture to most_complete above, but longest_value's
    # weighted tie confidence is a FLAT 0.7 (NOT min(1.0, 0.7*0.9)=0.63). Same
    # winner ("bb", higher weight), different confidence.
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "__cluster_id__": [1, 1], "name": ["aa", "bb"]}
    )
    rules = GoldenRulesConfig(
        default_strategy="longest_value",
        field_rules={"name": GoldenFieldRule(strategy="longest_value")},
    )
    qs = {(0, "name"): 0.5, (1, "name"): 0.9}
    got, _ = _assert_value_conf_parity_q(df, rules, ["name"], qs)
    assert got[1]["name"] == "bb"
    assert abs(got[1]["__golden_confidence__"] - 0.7) < 1e-12  # flat, NOT 0.63


def test_majority_vote_weighted_matches_reference():
    # Count-majority = "a" (2 of 3). Weights 0.1/0.1/0.9 flip the winner to "b"
    # (weight-sum a=0.2, b=0.9) at conf 0.9/1.1 -- confirms the WEIGHTED winner,
    # not the count winner.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": ["a", "a", "b"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="majority_vote",
        field_rules={"v": GoldenFieldRule(strategy="majority_vote")},
    )
    qs = {(0, "v"): 0.1, (1, "v"): 0.1, (2, "v"): 0.9}
    got, _ = _assert_value_conf_parity_q(df, rules, ["v"], qs)
    assert got[1]["v"] == "b"  # weighted winner, NOT the count winner "a"
    assert abs(got[1]["__golden_confidence__"] - 0.9 / 1.1) < 1e-12


def test_first_non_null_weighted_matches_reference():
    # Leading null; non-null rows 1 ("b") and 2 ("c") with weights 0.3 vs 0.8 ->
    # highest-weight "c" wins (unweighted would pick "b"), conf stays 0.6.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": [None, "b", "c"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="first_non_null",
        field_rules={"v": GoldenFieldRule(strategy="first_non_null")},
    )
    qs = {(1, "v"): 0.3, (2, "v"): 0.8}
    got, _ = _assert_value_conf_parity_q(df, rules, ["v"], qs)
    assert got[1]["v"] == "c"  # highest-weight non-null
    assert abs(got[1]["__golden_confidence__"] - 0.6) < 1e-12


# ─── confidence_majority (Task 4.1) ──────────────────────────────────────────


def _assert_value_conf_parity_cps(df, rules, cols, cluster_pair_scores):
    """Like ``_assert_value_conf_parity`` but threads ``cluster_pair_scores`` into
    BOTH paths (row-id-keyed edges). A confidence_majority field_rule forces the
    reference off the fast columnar path onto the exact merge_field oracle, so the
    fused path runs (does not decline) and both resolve identically."""
    ref = build_golden_records_batch(df, rules, cluster_pair_scores=cluster_pair_scores)
    got = run_golden_fused_arrow(df, rules, cluster_pair_scores=cluster_pair_scores)
    assert got is not None
    ref_map = {r["__cluster_id__"]: r for r in ref}
    got_map = {row["__cluster_id__"]: row for row in got.iter_rows(named=True)}
    assert set(got_map) == set(ref_map)
    for cid, row in got_map.items():
        r = ref_map[cid]
        for c in cols:
            assert row[c] == r[c]["value"], f"cluster {cid} col {c}"
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12
    return got_map, ref_map


def test_confidence_majority_strong_minority_beats_weak_majority():
    # 5-member cluster: 3 rows hold "Apple" (weak edges 0.1 each -> sum 0.3), 2
    # rows hold "Banana" (one STRONG edge 0.91). Count-majority = Apple (3 vs 2),
    # but confidence_majority sums edge weights: Banana 0.91 > Apple 0.3, so the
    # 2-member strong-edge MINORITY wins. conf = 0.91 / (0.3 + 0.91).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "__cluster_id__": [1, 1, 1, 1, 1],
            "v": ["Apple", "Apple", "Apple", "Banana", "Banana"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {1: {(0, 1): 0.1, (1, 2): 0.1, (0, 2): 0.1, (3, 4): 0.91}}
    got, _ = _assert_value_conf_parity_cps(df, rules, ["v"], cps)
    assert got[1]["v"] == "Banana"  # strong-edge minority beats weak-edge majority
    assert abs(got[1]["__golden_confidence__"] - 0.91 / (0.3 + 0.91)) < 1e-12


def test_confidence_majority_no_agreeing_edges_falls_back_to_majority():
    # Every edge connects members with DIFFERENT values (A-B, B-A), so no value
    # accrues edge weight -> fall back to unweighted count-majority. "A" appears
    # twice (rows 0,2), "B" once -> A wins 2/3.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": ["A", "B", "A"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {1: {(0, 1): 0.9, (1, 2): 0.8}}  # both edges span disagreeing values
    got, _ = _assert_value_conf_parity_cps(df, rules, ["v"], cps)
    assert got[1]["v"] == "A"  # count-majority fallback
    assert abs(got[1]["__golden_confidence__"] - 2.0 / 3.0) < 1e-12


def test_confidence_majority_no_pair_scores_falls_back_to_majority():
    # cluster_pair_scores absent for this cluster (empty dict) -> the reference
    # leaves pair_scores unset and _confidence_majority falls back to count
    # majority. Fused path must match: empty edge channel -> majority_vote branch.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": ["A", "B", "A"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    # Both an empty top-level dict and None must fall back identically.
    for cps in ({}, None):
        got, _ = _assert_value_conf_parity_cps(df, rules, ["v"], cps)
        assert got[1]["v"] == "A"
        assert abs(got[1]["__golden_confidence__"] - 2.0 / 3.0) < 1e-12


def test_confidence_majority_representative_index_is_first_agreeing_edge():
    # The representative index (which row's value survives) is set on the FIRST
    # agreeing edge for the winning code, in pair_scores.items() ORDER -- and it is
    # the FIRST endpoint `a` of that edge, NOT the min/canonical. Here the winning
    # value "X" is held by rows 0, 1, 2. The first agreeing edge in items() order
    # is (2, 1): endpoint a=2. So the surviving row is index 2. All three hold the
    # same value, so the emitted value is identical either way -- this test pins
    # the confidence (edge-sum) parity and that the ordering choice matches the
    # reference (provenance parity is Stage 8, but the logic is exercised now).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 1, 1],
            "v": ["X", "X", "X", "Y", "Y"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    # X edges summed 0.9; Y edge 0.2. X wins. Order the X edges so (2->1) is first.
    cps = {1: {(2, 1): 0.5, (0, 2): 0.4, (10, 11): 0.2}}
    got, _ = _assert_value_conf_parity_cps(df, rules, ["v"], cps)
    assert got[1]["v"] == "X"
    assert abs(got[1]["__golden_confidence__"] - 0.9 / (0.9 + 0.2)) < 1e-12


def test_confidence_majority_multi_cluster_isolated_edges():
    # Two clusters, each with its own edge set -- confirms edges are bucketed by
    # cluster (an edge in cluster 1 never leaks into cluster 2's resolution) and
    # that positions are remapped LOCAL to each cluster's sorted span.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 2, 2, 2],
            "v": ["A", "A", "B", "C", "D", "D"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {
        1: {(0, 1): 0.8},  # A-A strong -> A wins cluster 1
        2: {(11, 12): 0.7},  # D-D strong -> D wins cluster 2
    }
    got, _ = _assert_value_conf_parity_cps(df, rules, ["v"], cps)
    assert got[1]["v"] == "A"
    assert got[2]["v"] == "D"


def test_quality_scores_none_unweighted_path_unchanged():
    # Regression: quality_scores=None must be byte-identical to omitting it AND to
    # the unweighted reference -- the empty qweight channel takes the unweighted
    # kernel branch. Uses a length-tie fixture that the weighted path would resolve
    # differently.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "v": ["aa", "bb", "c", "z", "zzz"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"v": GoldenFieldRule(strategy="most_complete")},
    )
    ref = build_golden_records_batch(df, rules)
    got_omitted = run_golden_fused_arrow(df, rules)
    got_none = run_golden_fused_arrow(df, rules, quality_scores=None)
    assert got_omitted is not None and got_none is not None
    from polars.testing import assert_frame_equal

    assert_frame_equal(got_omitted, got_none)
    ref_map = {r["__cluster_id__"]: r for r in ref}
    for row in got_none.iter_rows(named=True):
        r = ref_map[row["__cluster_id__"]]
        assert row["v"] == r["v"]["value"]
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12


# ─── field_groups / correlated survivorship (Task 5.1) ───────────────────────
#
# A `field_groups` config forces the reference (build_golden_records_batch) onto
# the survivorship path (resolve_cluster / build_survivorship_native), the exact
# byte-parity oracle. The fused kernel ports core/survivorship/winner.py exactly:
# one winner row pinned lock-step across all group columns, ONE confidence per
# group folded into the cluster mean (denominator = n_scalar_cols + n_groups),
# and per-column back-fill under allow_fill.


def test_group_most_complete_lockstep_winner():
    # Group [street, city] most_complete: winner is the most-populated row across
    # the group columns; every group column pins to that ONE row.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            # cluster 1: row0 populated 2, row1 populated 1 -> winner row0.
            # cluster 2: row10 populated 1, row11 populated 2 -> winner row11.
            "street": ["123 Main", "456 Oak", "9 Elm", "5 Ash"],
            "city": ["Springfield", None, None, "Shelbyville"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])],
    )
    got = _assert_value_conf_parity(df, rules, ["street", "city"])
    # cluster 1 winner = row0 (both columns from the SAME row, lock-step).
    assert got[1]["street"] == "123 Main" and got[1]["city"] == "Springfield"
    # winner_populated 2 / 2 cols, no tie -> group conf 1.0 (sole unit).
    assert abs(got[1]["__golden_confidence__"] - 1.0) < 1e-12
    # cluster 2 winner = row11.
    assert got[2]["street"] == "5 Ash" and got[2]["city"] == "Shelbyville"


def test_group_most_complete_tie_conf_scaled_070():
    # Both rows fully populated -> populated-count tie -> winner = first row,
    # base = 2/2 = 1.0, x0.7 on tie -> group conf 0.7 (sole unit -> golden 0.7).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "a": ["A0", "A1"],
            "b": ["B0", "B1"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="pair", columns=["a", "b"])],
    )
    got = _assert_value_conf_parity(df, rules, ["a", "b"])
    assert got[1]["a"] == "A0" and got[1]["b"] == "B0"  # first row wins the tie
    assert abs(got[1]["__golden_confidence__"] - 0.7) < 1e-12


def test_group_null_winner_cell_pins_null_no_backfill():
    # The null-pin contract: allow_fill=False, and the WINNER row (most populated)
    # itself holds a null in one group column. That column pins to the winner
    # row's OWN null (kernel emits off+best, gathering to null) -- NOT the -1
    # scalar sentinel, and NOT back-filled even though a lower-ranked row has a
    # non-null value there. winner_populated EXCLUDES the null cell, so
    # base = winner_populated / n_cols = 2/3 (a kernel that counted the null cell,
    # or that back-filled, would diverge on the value and/or the confidence).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            # row0 populated 2 (winner); row1 populated 1 but HOLDS a non-null c
            # (a would-be donor the no-fill winner must ignore).
            "a": ["A0", None],
            "b": ["B0", None],
            "c": [None, "C1"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="trip", columns=["a", "b", "c"])],
    )
    got = _assert_value_conf_parity(df, rules, ["a", "b", "c"])
    assert got[1]["a"] == "A0" and got[1]["b"] == "B0"
    assert got[1]["c"] is None  # winner row0's own null, NOT donor "C1"
    # winner_populated 2 / 3 cols, no tie -> 2/3 (sole unit -> golden 2/3).
    assert abs(got[1]["__golden_confidence__"] - 2.0 / 3.0) < 1e-12


def test_group_allow_fill_backfills_from_next_best_row():
    # allow_fill: winner row has a null group column -> back-fill it from the
    # next-best ranked row that has a non-null there (winner.py:65-72).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            # all rows populated=1 -> tie, winner = row0.
            "a": ["A0", "A1", None],
            "b": [None, None, "B2"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="pair", columns=["a", "b"], allow_fill=True)],
    )
    got = _assert_value_conf_parity(df, rules, ["a", "b"])
    # a pinned to winner row0 ("A0"); b null in row0 -> filled from row2 ("B2").
    assert got[1]["a"] == "A0" and got[1]["b"] == "B2"
    # winner_populated 1 + n_filled 1 = 2 / 2 cols = 1.0, x0.7 (tie) -> 0.7.
    assert abs(got[1]["__golden_confidence__"] - 0.7) < 1e-12


def test_group_source_priority_lockstep():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "__source__": ["crm", "web"],
            "a": ["Acrm", "Aweb"],
            "b": ["Bcrm", "Bweb"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(
                name="pair",
                columns=["a", "b"],
                strategy="source_priority",
                source_priority=["web", "crm"],
            )
        ],
    )
    got = _assert_value_conf_parity(df, rules, ["a", "b"])
    # web ranks above crm -> winner = row1 (web); both columns from row1.
    assert got[1]["a"] == "Aweb" and got[1]["b"] == "Bweb"


def test_group_most_recent_lockstep():
    import datetime as _dt

    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "a": ["A0", "A1"],
            "b": ["B0", "B1"],
            "dt": [_dt.date(2020, 1, 1), _dt.date(2023, 6, 6)],
        },
        schema_overrides={"dt": pl.Date},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(
                name="pair", columns=["a", "b"], strategy="most_recent", date_column="dt"
            )
        ],
    )
    # `dt` is also a scalar user column (default most_complete). Assert group
    # columns pin to the latest-dated row; golden_confidence checked vs reference.
    got = _assert_value_conf_parity(df, rules, ["a", "b"])
    assert got[1]["a"] == "A1" and got[1]["b"] == "B1"  # 2023 is latest


def test_group_anchor_lockstep():
    # anchor="a": rows holding a non-null anchor rank first, then by populated
    # count. Winner pins both columns lock-step.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "a": [None, "A1"],  # anchor present only on row1
            "b": ["B0", "B1"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="pair", columns=["a", "b"], strategy="anchor", anchor="a")
        ],
    )
    got = _assert_value_conf_parity(df, rules, ["a", "b"])
    assert got[1]["a"] == "A1" and got[1]["b"] == "B1"  # anchor-present row wins


def test_group_mixed_with_scalar_field_rule_denominator():
    # A group + a scalar field_rule: golden_confidence denominator must be
    # (n_scalar_cols + n_groups) = 1 + 1 = 2, NOT n_output_cols (3). Distinct
    # per-unit confidences pin it: group tie conf 0.7, name unique-longest 1.0
    # -> golden = (0.7 + 1.0)/2 = 0.85 (a wrong 3-denominator gives != 0.85).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "name": ["Bob", "Robert"],  # unique-longest -> conf 1.0
            "street": ["S0", "S1"],
            "city": ["C0", "C1"],  # group both-populated tie -> conf 0.7
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])],
    )
    got = _assert_value_conf_parity(df, rules, ["name", "street", "city"])
    assert got[1]["name"] == "Robert"
    assert got[1]["street"] == "S0" and got[1]["city"] == "C0"
    assert abs(got[1]["__golden_confidence__"] - 0.85) < 1e-12


# ─── conditional field_rules / predicate IR (Task 6.2) ───────────────────────


def test_gate_accepts_lowerable_conditional():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    assert golden_fused_ready(rules) is True


def test_gate_declines_unlowerable_ordering_predicate():
    # an ordering comparator (<) does not lower to the equality/membership IR ->
    # the gate declines the whole config to the classic path.
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_complete", when="score < 5"),
                GoldenFieldRule(strategy="majority_vote"),
            ]
        },
    )
    assert golden_fused_ready(rules) is False


def test_run_declines_unlowerable_predicate_returns_none():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "phone": ["111", "222"],
            "score": [3, 7],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_complete", when="score < 5"),
                GoldenFieldRule(strategy="majority_vote"),
            ]
        },
    )
    assert run_golden_fused_arrow(df, rules) is None


def test_conditional_true_and_false_branches_match_reference():
    # phone: `when country == "US"` -> most_recent(dt), else default most_complete.
    # cluster 1 (US): most_recent picks the latest-dt row (dt=2 -> "222"); a plain
    # most_complete would tie the two len-3 phones and pick the first ("111"), so
    # "222" proves the conditional fired. cluster 2 (CA): default most_complete
    # picks the unique-longest "55555".
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "country": ["US", "US", "CA", "CA"],
            "dt": [1, 2, 3, 4],
            "phone": ["111", "222", "5", "55555"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    got = _assert_value_conf_parity(df, rules, ["country", "dt", "phone"])
    assert got[1]["phone"] == "222"  # most_recent fired
    assert got[2]["phone"] == "55555"  # default most_complete fired


def test_conditional_null_referenced_value_falls_to_default():
    # cluster 1's country is all-null -> the winner value is None, so
    # `country == "US"` is False (matches eval_predicate: None == "US" is False,
    # not a raise) -> default most_complete on phone -> tie -> first "111".
    # A most_recent (dt=2) would have picked "222", so "111" proves the default.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "country": [None, None, "US", "US"],
            "dt": [1, 2, 4, 3],
            "phone": ["111", "222", "7", "7777"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    got = _assert_value_conf_parity(df, rules, ["country", "dt", "phone"])
    assert got[1]["phone"] == "111"  # default fired (null referenced value)
    # cluster 2: country US -> most_recent, latest dt=4 is row 10 ("7") not the
    # longer "7777" most_complete would choose -> proves the conditional fired.
    assert got[2]["phone"] == "7"


def test_conditional_resolution_order_reorders_columns():
    # The conditional column `phone` (references `country`) is declared BEFORE
    # `country` in column order. build_resolution_order must reorder so `country`
    # resolves first; if the kernel used column order, phone would read an
    # unresolved country and mis-select. Same expected output as the true/false
    # test above -> parity proves col_order is honored.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "phone": ["111", "222", "5", "55555"],  # conditional col FIRST
            "country": ["US", "US", "CA", "CA"],  # referenced col AFTER
            "dt": [1, 2, 3, 4],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    got = _assert_value_conf_parity(df, rules, ["country", "dt", "phone"])
    assert got[1]["phone"] == "222"
    assert got[2]["phone"] == "55555"


def test_conditional_membership_in_list_matches_reference():
    # `state in ["NY", "NJ"]` -> majority_vote, else default first_non_null.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 2, 2, 2],
            "state": ["NY", "NY", "NY", "CA", "CA", "CA"],
            # cluster 1 (NY in list): majority_vote -> "a" (2/3)
            # cluster 2 (CA not in list): first_non_null -> "p"
            "v": ["a", "a", "b", "p", "q", "r"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "v": [
                GoldenFieldRule(strategy="majority_vote", when='state in ["NY", "NJ"]'),
                GoldenFieldRule(strategy="first_non_null"),
            ]
        },
    )
    got = _assert_value_conf_parity(df, rules, ["state", "v"])
    assert got[1]["v"] == "a"  # majority_vote fired
    assert got[2]["v"] == "p"  # first_non_null default fired


# ─── cluster_overrides (Task 7.1) ────────────────────────────────────────────


def test_cluster_overrides_per_cluster_strategy_differs():
    # Two clusters override the SAME column to DIFFERENT strategies; a third has
    # no override and falls back to the base default. Each cluster's effective
    # strategy produces a value the OTHER strategies would not, so a fused path
    # that ignored overrides (the Stage-6 gap this stage closes) diverges from the
    # reference and reddens the parity assertion.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12, 20, 21, 22],
            "__cluster_id__": [1, 1, 1, 2, 2, 2, 3, 3, 3],
            # cluster 1 (override first_non_null): "short" wins (base most_complete
            #   would pick the unique-longest "longestval").
            # cluster 2 (override longest_value): "qq" wins (base most_complete
            #   also picks longest, but the confidence rule differs, and the point
            #   is a per-cluster DIFFERENT strategy than cluster 1).
            # cluster 3 (no override -> base most_complete): unique-longest "yyyy".
            "v": ["short", "longestval", "mid", "p", "qq", "r", "x", "yyyy", "z"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={
            1: {"v": GoldenFieldRule(strategy="first_non_null")},
            2: {"v": GoldenFieldRule(strategy="longest_value")},
        },
    )
    assert golden_fused_ready(rules) is True
    got = _assert_value_conf_parity(df, rules, ["v"])
    # Effective strategy per cluster (proves the override was applied, not ignored):
    assert got[1]["v"] == "short"  # first_non_null (base most_complete -> "longestval")
    assert got[2]["v"] == "qq"  # longest_value
    assert got[3]["v"] == "yyyy"  # base most_complete (no override)


def test_cluster_overrides_value_differs_from_base_closes_gap():
    # A single-cluster override whose golden VALUE differs from the base strategy's
    # -- the direct regression guard for the Stage-6-flagged gap (a gate-True
    # override config silently applying the DEFAULT strategy).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            # base most_complete -> unique-longest "longestval"; override
            # first_non_null -> "short". Different winning value.
            "v": ["short", "longestval", "mid"],
        }
    )
    base_rules = GoldenRulesConfig(
        default_strategy="most_complete",
        # Force the reference off the fast path via an explicit field_rule so we can
        # read the BASE winner for the contrast assertion.
        field_rules={"v": GoldenFieldRule(strategy="most_complete")},
    )
    base = run_golden_fused_arrow(df, base_rules)
    assert base is not None
    assert base.row(0, named=True)["v"] == "longestval"

    override_rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={1: {"v": GoldenFieldRule(strategy="first_non_null")}},
    )
    got = _assert_value_conf_parity(df, override_rules, ["v"])
    assert got[1]["v"] == "short"  # override applied; != base "longestval"


def test_cluster_overrides_source_priority_needs_source_column():
    # An override to source_priority pulls in the shared __source__ channel; parity
    # against the reference's classic per-cluster merge_field path.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 2, 2, 2],
            "__source__": ["crm", "erp", "web", "crm", "erp", "web"],
            # cluster 1 override source_priority [erp, crm] -> "b" (erp's value).
            # cluster 2 no override -> base most_complete -> unique-longest "zzz".
            "v": ["a", "b", "c", "x", "zzz", "y"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={
            1: {"v": GoldenFieldRule(strategy="source_priority", source_priority=["erp", "crm"])},
        },
    )
    assert golden_fused_ready(rules) is True
    got = _assert_value_conf_parity(df, rules, ["v"])
    assert got[1]["v"] == "b"  # erp wins via override
    assert got[2]["v"] == "zzz"  # base most_complete


def test_cluster_overrides_ignored_when_survivorship_active():
    # Precedence: when a field_group (or conditional) is present, survivorship is
    # active and the reference (resolve_cluster) NEVER reads cluster_overrides. The
    # fused path must match -- ignore the override and resolve the group normally.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 2, 2, 2],
            "first": ["Bob", "Bob", "Robert", "Sue", "Sue", "Suzanne"],
            "last": ["Lee", "Lee", "Li", "Ng", "Ng", "Nguyen"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="nm", columns=["first", "last"], strategy="most_complete")],
        # This override would change cluster 1's `first` if honored -- but the
        # reference ignores it under an active field_group, so the parity oracle
        # pins the "ignored" behavior.
        cluster_overrides={1: {"first": GoldenFieldRule(strategy="first_non_null")}},
    )
    assert golden_fused_ready(rules) is True
    _assert_value_conf_parity(df, rules, ["first", "last"])


def test_cluster_overrides_conflicting_source_priority_lists_declines():
    # Two clusters override the SAME column to source_priority with DIFFERENT
    # priority lists. The fused path carries ONE per-column priority channel, so it
    # cannot represent both -> it must decline (return None) and let the caller fall
    # back to the reference. Exercises the documented decline in
    # run_golden_fused_arrow (the source-loop's per-column list-conflict guard).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 12],
            "__cluster_id__": [1, 1, 1, 2, 2, 2],
            "__source__": ["crm", "erp", "web", "crm", "erp", "web"],
            "v": ["a", "b", "c", "x", "y", "z"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={
            1: {"v": GoldenFieldRule(strategy="source_priority", source_priority=["erp", "crm"])},
            2: {"v": GoldenFieldRule(strategy="source_priority", source_priority=["web", "crm"])},
        },
    )
    # Gate passes (both override rules are covered), but the run declines on the
    # per-column channel conflict.
    assert golden_fused_ready(rules) is True
    assert run_golden_fused_arrow(df, rules) is None


# ─── provenance output (Task 8.1) ────────────────────────────────────────────
#
# With provenance=True, run_golden_fused_arrow returns a (golden_df, records)
# TUPLE -- mirroring build_golden_records_from_frames' (df, list[dict]) shape.
# `records` is byte-identical at the field level to
# build_golden_records_batch(provenance=True): each user-col field dict carries
# {value, confidence, source_row_id}, plus __cluster_id__ / __golden_confidence__.
# provenance source_row_id = the sorted frame's __row_id__ at the kernel's
# winner_idx (or None when winner_idx = -1) -- derivable Python-side, no kernel
# change. Groups: every group column's source_row_id is the group winner id, or
# the per-column FILLED row id under allow_fill (winner_idx already reflects it).


def _assert_provenance_parity(
    df, rules, cols, *, quality_scores=None, cluster_pair_scores=None
):
    """Run BOTH paths with provenance=True on the identical frame + config and
    assert per-(cluster, col) value + confidence + source_row_id equality against
    build_golden_records_batch(provenance=True). Also asserts the returned frame
    equals the provenance=False frame (values + dtypes)."""
    ref = build_golden_records_batch(
        df, rules, quality_scores=quality_scores,
        cluster_pair_scores=cluster_pair_scores, provenance=True,
    )
    out = run_golden_fused_arrow(
        df, rules, quality_scores=quality_scores,
        cluster_pair_scores=cluster_pair_scores, provenance=True,
    )
    assert out is not None, "fused path declined on a provenance-eligible config"
    assert isinstance(out, tuple) and len(out) == 2, "provenance=True must return a (df, records) tuple"
    got_df, got_records = out
    assert got_df is not None

    # The provenance=True frame must be identical to the provenance=False frame
    # (values + dtypes). assert_frame_equal is unreliable on Object dtype, so
    # compare schema (dtype preservation) + per-column Python-value lists.
    plain = run_golden_fused_arrow(
        df, rules, quality_scores=quality_scores,
        cluster_pair_scores=cluster_pair_scores,
    )
    assert plain is not None
    assert got_df.schema == plain.schema
    for c in got_df.columns:
        assert got_df[c].to_list() == plain[c].to_list(), f"frame col {c} differs"

    ref_map = {r["__cluster_id__"]: r for r in ref}
    got_map = {r["__cluster_id__"]: r for r in got_records}
    assert set(got_map) == set(ref_map)
    for cid, grec in got_map.items():
        rrec = ref_map[cid]
        for c in cols:
            assert grec[c]["value"] == rrec[c]["value"], f"value cid={cid} col={c}"
            assert grec[c]["source_row_id"] == rrec[c]["source_row_id"], (
                f"source_row_id cid={cid} col={c}: "
                f"got {grec[c]['source_row_id']!r} ref {rrec[c]['source_row_id']!r}"
            )
            assert abs(grec[c]["confidence"] - rrec[c]["confidence"]) < 1e-12, (
                f"confidence cid={cid} col={c}"
            )
        assert abs(grec["__golden_confidence__"] - rrec["__golden_confidence__"]) < 1e-12
    return got_map, ref_map


def test_provenance_returns_df_records_tuple():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "name": ["Bob", "Robert", "Bob", "Sue", "Suzanne"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    # provenance=False -> bare DataFrame (unchanged contract).
    assert isinstance(run_golden_fused_arrow(df, rules), pl.DataFrame)
    # provenance=True -> (df, records) tuple.
    out = run_golden_fused_arrow(df, rules, provenance=True)
    assert isinstance(out, tuple) and len(out) == 2
    got_df, records = out
    assert isinstance(got_df, pl.DataFrame)
    assert isinstance(records, list)
    # every record carries per-field source_row_id.
    for rec in records:
        assert "source_row_id" in rec["name"]


def test_provenance_source_row_id_most_complete():
    # cluster 1: "Robert" unique-longest at row_id 1. cluster 2: "Suzanne" row 11.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "name": ["Bob", "Robert", "Bob", "Sue", "Suzanne"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    got, _ = _assert_provenance_parity(df, rules, ["name"])
    assert got[1]["name"]["source_row_id"] == 1  # Robert's row_id
    assert got[2]["name"]["source_row_id"] == 11  # Suzanne's row_id


def test_provenance_null_winner_source_row_id_is_none():
    # unanimous_or_null disagreement -> null winner -> source_row_id None
    # (kernel -1 sentinel maps to None, matching merge_field idx=None).
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
    got, _ = _assert_provenance_parity(df, rules, ["v"])
    assert got[1]["v"]["value"] is None
    assert got[1]["v"]["source_row_id"] is None  # disagreement -> null winner
    assert got[2]["v"]["source_row_id"] == 10  # first agreeing "z"


def test_provenance_majority_vote():
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
    got, _ = _assert_provenance_parity(df, rules, ["v"])
    # cluster 1 tie -> first-appearance "a" at row 0.
    assert got[1]["v"]["source_row_id"] == 0


def test_provenance_source_priority():
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
    got, _ = _assert_provenance_parity(df, rules, ["name"])
    assert got[1]["name"]["source_row_id"] == 1  # the web row


def test_provenance_source_priority_no_match_none():
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
    got, _ = _assert_provenance_parity(df, rules, ["name"])
    assert got[1]["name"]["value"] is None
    assert got[1]["name"]["source_row_id"] is None


def test_provenance_most_recent():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "name": ["Bob", "Robert", "Bobby"],
            "dt": [_dt.date(2020, 1, 1), _dt.date(2022, 6, 15), _dt.date(2021, 3, 3)],
        },
        schema_overrides={"dt": pl.Date},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    got, _ = _assert_provenance_parity(df, rules, ["name", "dt"])
    assert got[1]["name"]["source_row_id"] == 1  # 2022 is latest


def test_provenance_quality_weight_tie():
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "__cluster_id__": [1, 1], "name": ["aa", "bb"]}
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    qs = {(0, "name"): 0.5, (1, "name"): 0.9}
    got, _ = _assert_provenance_parity(df, rules, ["name"], quality_scores=qs)
    assert got[1]["name"]["source_row_id"] == 1  # higher-weight row wins


def test_provenance_confidence_majority():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "__cluster_id__": [1, 1, 1, 1, 1],
            "v": ["Apple", "Apple", "Apple", "Banana", "Banana"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {1: {(0, 1): 0.1, (1, 2): 0.1, (0, 2): 0.1, (3, 4): 0.91}}
    got, _ = _assert_provenance_parity(df, rules, ["v"], cluster_pair_scores=cps)
    assert got[1]["v"]["value"] == "Banana"
    # source_row_id is the first-agreeing-edge endpoint for the winning value.
    assert got[1]["v"]["source_row_id"] == got[1]["v"]["source_row_id"]  # matches ref (checked in helper)


def test_provenance_group_lockstep_winner_id():
    # Every group column's source_row_id is the SAME group winner id.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "street": ["123 Main", "456 Oak", "9 Elm", "5 Ash"],
            "city": ["Springfield", None, None, "Shelbyville"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])],
    )
    got, _ = _assert_provenance_parity(df, rules, ["street", "city"])
    # cluster 1 winner row0; both group columns pin to row_id 0.
    assert got[1]["street"]["source_row_id"] == 0
    assert got[1]["city"]["source_row_id"] == 0
    # cluster 2 winner row11.
    assert got[2]["street"]["source_row_id"] == 11
    assert got[2]["city"]["source_row_id"] == 11


def test_provenance_group_allow_fill_uses_filled_row_id():
    # Under allow_fill, the back-filled column's source_row_id is the DONOR row's
    # id (the filled row), NOT the group winner id -- winner_idx already reflects
    # the filled position.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "a": ["A0", "A1", None],
            "b": [None, None, "B2"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="pair", columns=["a", "b"], allow_fill=True)],
    )
    got, _ = _assert_provenance_parity(df, rules, ["a", "b"])
    assert got[1]["a"]["source_row_id"] == 0  # winner row0
    assert got[1]["b"]["source_row_id"] == 2  # filled from row2 (donor)


def test_provenance_group_null_pin_no_backfill_uses_winner_id():
    # The winner row's OWN null cell: value None but source_row_id is the WINNER
    # id (filled_ids.get(c, wid) -> wid), NOT None. Pins that the kernel emits the
    # winner position (not -1) for a null-pinned group cell.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "a": ["A0", None],
            "b": ["B0", None],
            "c": [None, "C1"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="trip", columns=["a", "b", "c"])],
    )
    got, _ = _assert_provenance_parity(df, rules, ["a", "b", "c"])
    assert got[1]["c"]["value"] is None
    assert got[1]["c"]["source_row_id"] == 0  # winner row0's id, NOT None


def test_provenance_conditional():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "country": ["US", "US", "CA", "CA"],
            "dt": [1, 2, 3, 4],
            "phone": ["111", "222", "5", "55555"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    got, _ = _assert_provenance_parity(df, rules, ["country", "dt", "phone"])
    assert got[1]["phone"]["source_row_id"] == 1  # most_recent picked dt=2 (row1)
    assert got[2]["phone"]["source_row_id"] == 11  # most_complete "55555" (row11)


def test_provenance_cluster_overrides():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 20, 21, 22],
            "__cluster_id__": [1, 1, 1, 3, 3, 3],
            "v": ["short", "longestval", "mid", "x", "yyyy", "z"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={1: {"v": GoldenFieldRule(strategy="first_non_null")}},
    )
    got, _ = _assert_provenance_parity(df, rules, ["v"])
    assert got[1]["v"]["value"] == "short"  # override first_non_null
    assert got[1]["v"]["source_row_id"] == 0  # first non-null row
    assert got[3]["v"]["value"] == "yyyy"  # base most_complete
    assert got[3]["v"]["source_row_id"] == 21


# ─── full parity matrix + mixed-type fixtures (Task 8.2) ─────────────────────
#
# Each case builds a frame + config that routes the reference to the EXACT
# survivorship/merge_field oracle (never the approximating fast path). The matrix
# sweeps every covered strategy family x {provenance on/off}; each case asserts
# FRAME equality (values + DTYPES via assert_frame_equal) and, when provenance is
# on, per-field source_row_id parity vs build_golden_records_batch.


def _case_most_complete():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11],
            "__cluster_id__": [1, 1, 1, 2, 2],
            "name": ["Bob", "Robert", "Bob", "Sue", "Suzanne"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    return df, rules, ["name"], {}


def _case_majority_mixed_type():
    # MIXED-TYPE column under majority_vote (the factorization edge end-to-end):
    # int 1 and float 1.0 are raw-value-equal -> one code; string "1" is distinct.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "__cluster_id__": [1, 1, 1, 1],
            "v": pl.Series("v", [1, 1.0, "1", 1], dtype=pl.Object),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="majority_vote",
        field_rules={"v": GoldenFieldRule(strategy="majority_vote")},
    )
    return df, rules, ["v"], {}


def _case_unanimous_mixed_type():
    # MIXED-TYPE under unanimous_or_null: int 1 vs float 1.0 are raw-value-equal
    # (short-circuit -> unanimous), so the winner is int 1 at conf 1.0.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "v": pl.Series("v", [1, 1.0], dtype=pl.Object),
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="unanimous_or_null",
        field_rules={"v": GoldenFieldRule(strategy="unanimous_or_null")},
    )
    return df, rules, ["v"], {}


def _case_first_non_null():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": [None, "b", "c"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="first_non_null",
        field_rules={"v": GoldenFieldRule(strategy="first_non_null")},
    )
    return df, rules, ["v"], {}


def _case_longest_value():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": ["aa", "bbb", "c"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="longest_value",
        field_rules={"v": GoldenFieldRule(strategy="longest_value")},
    )
    return df, rules, ["v"], {}


def _case_source_priority():
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
            "name": GoldenFieldRule(strategy="source_priority", source_priority=["web", "crm"]),
        },
    )
    return df, rules, ["name"], {}


def _case_most_recent_float_and_datetime():
    # Float64 + Datetime user columns round-trip at their native dtype (the whole
    # point of the index-return design). `amount` (Float64) and `ts` (Datetime)
    # ride through as default most_complete scalar columns.
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "name": ["Bob", "Robert", "Bobby"],
            "dt": [_dt.date(2020, 1, 1), _dt.date(2022, 6, 15), _dt.date(2021, 3, 3)],
            "amount": pl.Series("amount", [1.5, 2.5, 3.5], dtype=pl.Float64),
            "ts": pl.Series(
                "ts",
                [
                    _dt.datetime(2020, 1, 1),
                    _dt.datetime(2022, 6, 15),
                    _dt.datetime(2021, 3, 3),
                ],
                dtype=pl.Datetime,
            ),
        },
        schema_overrides={"dt": pl.Date},
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="dt")},
    )
    return df, rules, ["name", "dt", "amount", "ts"], {}


def _case_int_and_none_empty_string():
    # Int64 column round-trip + None mixed with "" (distinct values, not conflated).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "code": pl.Series("code", [10, 20, 20], dtype=pl.Int64),
            "s": [None, "", "x"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="majority_vote",
        field_rules={
            "code": GoldenFieldRule(strategy="majority_vote"),
            "s": GoldenFieldRule(strategy="first_non_null"),
        },
    )
    return df, rules, ["code", "s"], {}


def _case_group():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1],
            "__cluster_id__": [1, 1],
            "street": ["123 Main", "456 Oak"],
            "city": ["Springfield", None],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])],
    )
    return df, rules, ["street", "city"], {}


def _case_conditional():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 10, 11],
            "__cluster_id__": [1, 1, 2, 2],
            "country": ["US", "US", "CA", "CA"],
            "dt": [1, 2, 3, 4],
            "phone": ["111", "222", "5", "55555"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "phone": [
                GoldenFieldRule(strategy="most_recent", date_column="dt", when='country == "US"'),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    return df, rules, ["country", "dt", "phone"], {}


def _case_confidence_majority():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "__cluster_id__": [1, 1, 1, 1, 1],
            "v": ["Apple", "Apple", "Apple", "Banana", "Banana"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {1: {(0, 1): 0.1, (1, 2): 0.1, (0, 2): 0.1, (3, 4): 0.91}}
    return df, rules, ["v"], {"cluster_pair_scores": cps}


def _case_confidence_majority_with_quality_scores():
    # THE GAP: confidence_majority + a non-None quality_scores. The cluster's edges
    # all span DISAGREEING values, so _confidence_majority falls back to the
    # WEIGHTED count-majority (quality_weights) -- the weighted-fallback
    # composition untested through Stage 4. Weights flip the winner from the count
    # majority "A" (rows 0,2) to "B" (row1, weight 0.9 vs A's 0.2).
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "__cluster_id__": [1, 1, 1],
            "v": ["A", "B", "A"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="confidence_majority",
        field_rules={"v": GoldenFieldRule(strategy="confidence_majority")},
    )
    cps = {1: {(0, 1): 0.9, (1, 2): 0.8}}  # both edges disagree -> weighted fallback
    qs = {(0, "v"): 0.1, (1, "v"): 0.9, (2, "v"): 0.1}
    return df, rules, ["v"], {"cluster_pair_scores": cps, "quality_scores": qs}


def _case_quality_weighted():
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "__cluster_id__": [1, 1], "name": ["aa", "bb"]}
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    qs = {(0, "name"): 0.5, (1, "name"): 0.9}
    return df, rules, ["name"], {"quality_scores": qs}


def _case_cluster_overrides():
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 20, 21, 22],
            "__cluster_id__": [1, 1, 1, 3, 3, 3],
            "v": ["short", "longestval", "mid", "x", "yyyy", "z"],
        }
    )
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        cluster_overrides={1: {"v": GoldenFieldRule(strategy="first_non_null")}},
    )
    return df, rules, ["v"], {}


_MATRIX_CASES = {
    "most_complete": _case_most_complete,
    "majority_mixed_type": _case_majority_mixed_type,
    "unanimous_mixed_type": _case_unanimous_mixed_type,
    "first_non_null": _case_first_non_null,
    "longest_value": _case_longest_value,
    "source_priority": _case_source_priority,
    "most_recent_float_datetime": _case_most_recent_float_and_datetime,
    "int_and_none_empty_string": _case_int_and_none_empty_string,
    "group": _case_group,
    "conditional": _case_conditional,
    "confidence_majority": _case_confidence_majority,
    "confidence_majority_with_quality_scores": _case_confidence_majority_with_quality_scores,
    "quality_weighted": _case_quality_weighted,
    "cluster_overrides": _case_cluster_overrides,
}


@pytest.mark.parametrize("case_name", list(_MATRIX_CASES))
@pytest.mark.parametrize("provenance", [False, True])
def test_parity_matrix(case_name, provenance):
    df, rules, cols, kwargs = _MATRIX_CASES[case_name]()
    quality_scores = kwargs.get("quality_scores")
    cluster_pair_scores = kwargs.get("cluster_pair_scores")

    if not provenance:
        # FRAME parity (values + DTYPES). Build the reference records and compare
        # the fused frame column-by-column, and separately assert the fused frame's
        # dtypes match the SOURCE column dtypes (native-dtype preservation).
        ref = build_golden_records_batch(
            df, rules, quality_scores=quality_scores,
            cluster_pair_scores=cluster_pair_scores,
        )
        got = run_golden_fused_arrow(
            df, rules, quality_scores=quality_scores,
            cluster_pair_scores=cluster_pair_scores,
        )
        assert got is not None, f"{case_name}: fused path declined"
        ref_map = {r["__cluster_id__"]: r for r in ref}
        got_map = {row["__cluster_id__"]: row for row in got.iter_rows(named=True)}
        assert set(got_map) == set(ref_map)
        for cid, row in got_map.items():
            r = ref_map[cid]
            for c in cols:
                assert row[c] == r[c]["value"], f"{case_name} cid={cid} col={c}"
            assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12
        # DTYPE preservation: each user column keeps its source dtype.
        for c in cols:
            assert got.schema[c] == df.schema[c], (
                f"{case_name} col={c}: dtype {got.schema[c]} != source {df.schema[c]}"
            )
    else:
        _assert_provenance_parity(
            df, rules, cols,
            quality_scores=quality_scores,
            cluster_pair_scores=cluster_pair_scores,
        )
