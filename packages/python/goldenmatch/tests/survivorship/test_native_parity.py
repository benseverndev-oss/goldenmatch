from datetime import date

import polars as pl
from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch
from goldenmatch.core.survivorship.native import survivorship_native_eligible


def _slow_oracle(multi_df, rules):
    """Slow path on a __row_id__-deterministic frame; returns the golden
    DataFrame (values + __golden_confidence__; provenance=False)."""
    df = multi_df.sort(["__cluster_id__", "__row_id__"])
    rows = build_golden_records_batch(df, rules, provenance=False)
    golden = []
    for rec in rows:
        row = {"__cluster_id__": rec["__cluster_id__"],
               "__golden_confidence__": rec.get("__golden_confidence__")}
        for col, info in rec.items():
            if col in ("__cluster_id__", "__golden_confidence__", "__survivorship_prov__"):
                continue
            row[col] = info["value"] if isinstance(info, dict) and "value" in info else info
        golden.append(row)
    return pl.DataFrame(golden).sort("__cluster_id__")


def assert_parity(multi_df, rules, compare_confidence=True):
    """Byte-identical golden output: native path == slow oracle (provenance=False)."""
    from goldenmatch.core.survivorship.native import build_survivorship_native
    native = build_survivorship_native(multi_df, rules).sort("__cluster_id__")
    oracle = _slow_oracle(multi_df, rules)
    cols = sorted(c for c in oracle.columns if compare_confidence or c != "__golden_confidence__")
    assert native.select(cols).equals(oracle.select(cols)), (
        f"PARITY MISMATCH\nnative:\n{native.select(cols)}\noracle:\n{oracle.select(cols)}"
    )


def test_eligible_false_until_implemented():
    rules = GoldenRulesConfig(default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])])
    assert survivorship_native_eligible(rules, provenance=False) is False


def test_slow_path_deterministic_on_ties():
    # tie-heavy: 2-row clusters where both rows have the same populated count
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "street": ["A St", "B St", "C St", "D St"],   # both rows in each cluster 2/2 -> tie
        "city": ["LA", "NY", "SF", "DC"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])])
    a = _slow_oracle(df, rules)
    b = _slow_oracle(df.sample(fraction=1.0, shuffle=True, seed=1), rules)
    assert a.equals(b)   # winner = lowest __row_id__ regardless of input order


# ──────────────────────────────────────────────────────────────────────────
# B1: most_complete group resolution
# ──────────────────────────────────────────────────────────────────────────


def _most_complete_rules():
    # Every user column is a group member (no scalar resolution in Phase B).
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="addr", strategy="most_complete",
                            columns=["street", "city", "zip"]),
        ],
    )


def test_b1_most_complete_no_frankenstein():
    # cluster 1: row A has the LONGEST street but is least complete (1/3);
    # row B is most-complete (3/3). The group winner is row B and EVERY column
    # must come from row B in lock-step -- a Frankenstein merge would take A's
    # long street and B's city/zip.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": ["123 Main Street Apartment 4B Building 7", "5 Oak Rd"],
        "city": [None, "New York"],
        "zip": [None, "10001"],
    })
    assert_parity(df, _most_complete_rules(), compare_confidence=False)


def test_b1_most_complete_tie_lowest_row_id():
    # cluster 1: both rows 2/3 populated -> tie -> lowest __row_id__ (10) wins.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [11, 10],   # deliberately out of order in the frame
        "street": ["A St", "B St"],
        "city": ["LA", "NY"],
        "zip": [None, None],
    })
    assert_parity(df, _most_complete_rules(), compare_confidence=False)


def test_b1_most_complete_all_null_group():
    # cluster 1: every group column null in both rows -> winner is row 0
    # (lowest __row_id__) and all pinned values are null.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": [None, None],
        "city": [None, None],
        "zip": [None, None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "street": pl.Utf8, "city": pl.Utf8, "zip": pl.Utf8})
    assert_parity(df, _most_complete_rules(), compare_confidence=False)


def test_b1_most_complete_multi_cluster_mixed():
    # Multiple clusters, mixed completeness + a clear winner each.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2, 2, 3],
        "__row_id__": [10, 11, 20, 21, 22, 30],
        "street": ["1 A St", "2 B Ave", None, "9 Z Rd", "9 Z Rd", "solo"],
        "city": ["LA", None, "SF", "SF", None, "DC"],
        "zip": [None, "02139", "94103", None, None, "20001"],
    })
    assert_parity(df, _most_complete_rules(), compare_confidence=False)


# ──────────────────────────────────────────────────────────────────────────
# B2: source_priority / most_recent / anchor group resolution
# ──────────────────────────────────────────────────────────────────────────


def _source_priority_rules():
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="addr", strategy="source_priority",
                            source_priority=["crm", "erp", "web"],
                            columns=["street", "city"]),
        ],
    )


def test_b2_source_priority_winner_and_lockstep():
    # cluster 1: erp(idx1) and crm(idx0) present -> crm wins; lock-step pin.
    # cluster 2: web(idx2) and crm(idx0) -> crm wins.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "__source__": ["erp", "crm", "web", "crm"],
        "street": ["1 Erp St", "2 Crm Ave", "3 Web Rd", "4 Crm Ln"],
        "city": ["LA", "NY", "SF", "DC"],
    })
    assert_parity(df, _source_priority_rules(), compare_confidence=False)


def test_b2_source_priority_tie_same_source_lowest_row_id():
    # Both rows same source ("crm") -> rank tie -> lowest __row_id__ wins.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [11, 10],   # frame order swapped
        "__source__": ["crm", "crm"],
        "street": ["1 A St", "2 B Ave"],
        "city": ["LA", "NY"],
    })
    assert_parity(df, _source_priority_rules(), compare_confidence=False)


def test_b2_source_priority_unknown_source_ranks_last():
    # cluster 1: one row from an unknown source ("legacy", -> sentinel) and one
    # from "web" (idx2). web outranks the unknown source.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "__source__": ["legacy", "web", "ghost", "spooky"],  # cluster 2: both unknown -> lowest row_id
        "street": ["1 Old St", "2 Web Ave", "3 G St", "4 S Ave"],
        "city": ["LA", "NY", "SF", "DC"],
    })
    assert_parity(df, _source_priority_rules(), compare_confidence=False)


def _most_recent_rules():
    # The date column is itself a group member so every user column is grouped.
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="addr", strategy="most_recent",
                            date_column="updated",
                            columns=["street", "city", "updated"]),
        ],
    )


def test_b2_most_recent_latest_wins_lockstep():
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "street": ["1 Old St", "2 New Ave", "3 A Rd", "4 B Ln"],
        "city": ["LA", "NY", "SF", "DC"],
        "updated": [date(2020, 1, 1), date(2023, 6, 1),
                    date(2024, 1, 1), date(2021, 1, 1)],
    })
    assert_parity(df, _most_recent_rules(), compare_confidence=False)


def test_b2_most_recent_null_dates_last():
    # cluster 1: one null date, one real date -> real date wins (nulls last).
    # cluster 2: both null -> lowest __row_id__ wins.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 21, 20],
        "street": ["1 A St", "2 B Ave", "3 C Rd", "4 D Ln"],
        "city": ["LA", "NY", "SF", "DC"],
        "updated": [None, date(2022, 1, 1), None, None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "street": pl.Utf8, "city": pl.Utf8, "updated": pl.Date})
    assert_parity(df, _most_recent_rules(), compare_confidence=False)


def test_b2_most_recent_tie_same_date_lowest_row_id():
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [11, 10],   # frame order swapped
        "street": ["1 A St", "2 B Ave"],
        "city": ["LA", "NY"],
        "updated": [date(2022, 5, 5), date(2022, 5, 5)],
    })
    assert_parity(df, _most_recent_rules(), compare_confidence=False)


def _anchor_rules():
    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="addr", strategy="anchor", anchor="zip",
                            columns=["street", "city", "zip"]),
        ],
    )


def test_b2_anchor_present_wins_over_more_complete():
    # cluster 1: row A is more complete (street+city, no zip) but lacks the
    # anchor; row B has the anchor (zip). anchor-present beats completeness.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": ["1 Full St", "2 B Ave"],
        "city": ["LA", None],
        "zip": [None, "10001"],
    })
    assert_parity(df, _anchor_rules(), compare_confidence=False)


def test_b2_anchor_both_present_breaks_by_completeness():
    # Both rows carry the anchor -> tie on present -> most-complete wins,
    # then lowest __row_id__ on a full tie.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "street": [None, "2 B Ave", "3 C Rd", "4 D Ln"],   # cluster1: row B more complete
        "city": [None, "NY", "SF", "DC"],
        "zip": ["10001", "10002", "94103", "20001"],        # cluster2: both full -> row_id 20
    })
    assert_parity(df, _anchor_rules(), compare_confidence=False)


def test_b2_anchor_none_present_degrades_to_most_complete():
    # No row carries the anchor (zip all null) -> ranking degrades to
    # most_complete (then lowest __row_id__ on tie).
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": ["1 A St", "2 B Ave Longer"],
        "city": [None, "NY"],   # row B (row_id 11) more complete
        "zip": [None, None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "street": pl.Utf8, "city": pl.Utf8, "zip": pl.Utf8})
    assert_parity(df, _anchor_rules(), compare_confidence=False)


# ──────────────────────────────────────────────────────────────────────────
# B3: allow_fill per-cell back-fill
# ──────────────────────────────────────────────────────────────────────────


def test_b3_most_complete_allow_fill_backfills_winner_null():
    # cluster 1: winner = row A (most complete, 2/3). zip is null on the winner
    # but row B has a zip -> allow_fill back-fills zip from row B. street/city
    # still come from the winner.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": ["1 A St", "2 B Ave"],
        "city": ["LA", None],
        "zip": [None, "10001"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      allow_fill=True,
                                      columns=["street", "city", "zip"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_b3_allow_fill_no_donor_stays_null():
    # winner is null on zip and NO other row has a zip -> stays null.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "street": ["1 A St", "2 B Ave"],
        "city": ["LA", "NY"],
        "zip": [None, None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "street": pl.Utf8, "city": pl.Utf8, "zip": pl.Utf8})
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      allow_fill=True,
                                      columns=["street", "city", "zip"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_b3_source_priority_allow_fill_walks_ranking():
    # Winner is the crm row but its city is null; the fill walks the source
    # ranking (crm -> erp -> web) for the first non-null city.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "__source__": ["web", "crm", "erp"],
        "street": ["1 Web St", "2 Crm Ave", "3 Erp Rd"],
        "city": ["LA", None, "Boston"],   # crm wins, city null -> erp (next in priority) fills "Boston"
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="source_priority",
                                      source_priority=["crm", "erp", "web"],
                                      allow_fill=True,
                                      columns=["street", "city"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_b3_most_recent_allow_fill():
    # Latest row wins; its city is null -> filled from the next-most-recent
    # row that has a city.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "street": ["1 Old St", "2 Mid Ave", "3 New Rd"],
        "city": ["LA", "NY", None],   # newest (row 12) city null -> next-newest (row 11) "NY"
        "updated": [date(2020, 1, 1), date(2022, 1, 1), date(2024, 1, 1)],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_recent",
                                      date_column="updated", allow_fill=True,
                                      columns=["street", "city", "updated"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_b3_anchor_allow_fill():
    # anchor-present row wins; one of its non-anchor columns is null and is
    # back-filled walking the anchor ranking.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "street": ["1 Full St", None, "3 C Rd"],
        "city": ["LA", "NY", "SF"],
        "zip": [None, "10001", None],   # row 11 has anchor -> wins; its street null -> fill
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="anchor",
                                      anchor="zip", allow_fill=True,
                                      columns=["street", "city", "zip"])],
    )
    assert_parity(df, rules, compare_confidence=False)
