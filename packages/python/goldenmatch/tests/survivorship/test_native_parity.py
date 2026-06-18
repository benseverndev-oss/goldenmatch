from datetime import date

import polars as pl
from goldenmatch.config.schemas import (
    GoldenFieldRule,
    GoldenGroupRule,
    GoldenRulesConfig,
)
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


def test_b3_allow_fill_donor_not_lowest_row_id():
    # The fill donor is NOT the lowest __row_id__ row that has the column, so a
    # naive "lowest row_id non-null" fill would diverge from the ranking walk.
    # One cluster, most_complete + allow_fill, columns street/city/zip:
    #   row 10 (A): completeness 1/3 (only zip) -- DECOY: lowest row_id, zip set.
    #   row 11 (B): completeness 2/3 (street+city), zip NULL  -> WINNER.
    #   row 12 (C): completeness 2/3 (street+zip), city NULL  -> ranking-correct
    #               donor for zip (ranks above A, BELOW B, higher row_id than A).
    # Ranking (completeness desc, row_id asc): B(11) -> C(12) -> A(10).
    # B.zip is null, so the walk fills from C (zip "10001"), NOT from the
    # lower-row_id decoy A (zip "99999").
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "street": [None, "11 Win St", "12 Don Rd"],
        "city": [None, "NY", None],
        "zip": ["99999", None, "10001"],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "street": pl.Utf8, "city": pl.Utf8, "zip": pl.Utf8})
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      allow_fill=True,
                                      columns=["street", "city", "zip"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_empty_frame_returns_empty():
    from goldenmatch.core.survivorship.native import build_survivorship_native
    df = pl.DataFrame(
        {"__cluster_id__": [], "__row_id__": [], "street": [], "city": [], "zip": []},
        schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
                "street": pl.Utf8, "city": pl.Utf8, "zip": pl.Utf8},
    )
    result = build_survivorship_native(df, _most_complete_rules())
    assert result.height == 0


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


# ──────────────────────────────────────────────────────────────────────────
# C1: scalar field resolution (groups + plain scalar fields)
# ──────────────────────────────────────────────────────────────────────────


def test_c1_default_strategy_scalar_alongside_group():
    # A group (addr) PLUS a scalar (name) that falls through to default_strategy
    # (most_complete). name must resolve to the longest non-null value per
    # cluster (tie -> lowest __row_id__), independent of the group winner.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "street": ["1 A St", "2 B Ave Longer", None, "9 Z Rd"],
        "city": ["LA", None, "SF", None],
        "name": ["Bob", "Robert Smith", None, "Al"],   # longest wins per cluster
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      columns=["street", "city"])],
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_first_non_null():
    # Per-field first_non_null: lowest-__row_id__ non-null wins. Frame order is
    # scrambled to prove the pick is row_id-stable, not input-order-dependent.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1, 2, 2],
        "__row_id__": [12, 10, 11, 21, 20],   # out of order
        "email": ["c@x.com", None, "b@x.com", None, "z@x.com"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"email": GoldenFieldRule(strategy="first_non_null")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_most_complete_tie_lowest_row_id():
    # Two equal-length non-null candidates in a cluster -> tie -> lowest row_id.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [11, 10],   # swapped
        "name": ["Abcd", "Wxyz"],   # same length -> tie -> row_id 10 ("Wxyz")
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_longest_value():
    # longest_value picks the longest non-null string (same VALUE as
    # most_complete; confidence differs but Phase C compares values only).
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "title": ["VP", "Vice President of Sales", "Eng", None],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"title": GoldenFieldRule(strategy="longest_value")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_most_recent():
    # most_recent: value at the max date among rows where BOTH value and date
    # are non-null. Cluster 2 has the newest row's value null -> falls to the
    # next-newest with a value.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "phone": ["111", "222", "333", None],   # cluster2 newest (date 2024) is null
        "updated": [date(2020, 1, 1), date(2023, 1, 1),
                    date(2022, 1, 1), date(2024, 1, 1)],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="most_recent",
                                              date_column="updated")},
        # updated is itself a scalar resolved by default_strategy (most_complete).
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_most_recent_null_date_excluded():
    # A row with a value but a NULL date is excluded from most_recent
    # (merge_field requires both non-null), even if it is the lowest row_id.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "phone": ["aaa", "bbb", "ccc"],
        "updated": [None, date(2021, 1, 1), date(2023, 1, 1)],  # row 12 newest
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "phone": pl.Utf8, "updated": pl.Date})
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="most_recent",
                                              date_column="updated")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_source_priority():
    # source_priority over [crm, erp, web]: best-ranked source whose
    # first-occurrence row is non-null wins.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1, 2, 2],
        "__row_id__": [10, 11, 12, 20, 21],
        "__source__": ["web", "erp", "crm", "erp", "web"],
        "phone": ["1web", "2erp", "3crm", "4erp", "5web"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="source_priority",
                                              source_priority=["crm", "erp", "web"])},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_source_priority_first_occurrence_null_blocks():
    # crm's FIRST occurrence (lowest row_id) is null. merge_field records the
    # first occurrence's (null) value and SKIPS crm, even though a later crm row
    # is populated -> erp wins. A naive "first non-null crm" would diverge.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "__source__": ["crm", "erp", "crm"],
        "phone": [None, "2erp", "3crm"],   # crm first occ (row 10) null -> erp wins
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "__source__": pl.Utf8, "phone": pl.Utf8})
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="source_priority",
                                              source_priority=["crm", "erp", "web"])},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_source_priority_unknown_source_no_winner():
    # Every row is an unknown source -> no priority match -> None
    # (merge_field._source_priority fallback returns None).
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "__source__": ["legacy", "ghost"],
        "phone": ["1old", "2spooky"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="source_priority",
                                              source_priority=["crm", "erp", "web"])},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_all_null():
    # Every value of a scalar is null across the cluster -> resolves to None.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "name": [None, None, "Has Name", None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "name": pl.Utf8})
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_all_agree_short_circuit():
    # All non-null values identical -> merge_field short-circuits to the value
    # regardless of strategy. Value parity holds (the value is the same pick).
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "code": ["X1", "X1", None],
    }, schema={"__cluster_id__": pl.Int64, "__row_id__": pl.Int64,
               "code": pl.Utf8})
    rules = GoldenRulesConfig(
        default_strategy="first_non_null",
        field_rules={"code": GoldenFieldRule(strategy="most_complete")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_scalar_validate_mask_skips_invalid():
    # email_validate pre-mask: an INVALID email is dropped to null before the
    # agg, so most_complete must NOT pick the (longer) invalid value -- it picks
    # the longest VALID email instead. The slow path filters candidates the same
    # way, so native == oracle.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1, 2, 2],
        "__row_id__": [10, 11, 12, 20, 21],
        # cluster1: "not-an-email-but-very-long" is longest but INVALID ->
        # dropped; "alice@example.com" is the longest valid one.
        # cluster2: both invalid -> all masked to null -> resolves to None.
        "email": ["a@x.io", "not-an-email-but-very-long", "alice@example.com",
                  "bad", "also bad"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"email": GoldenFieldRule(strategy="most_complete",
                                              validate="email_validate")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_validate_mask_with_first_non_null():
    # validate + first_non_null: the lowest-row_id row has an INVALID email
    # (masked to null) so first_non_null must skip it and take the next valid.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "email": ["garbage", "bob@example.com", "carol@example.com"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"email": GoldenFieldRule(strategy="first_non_null",
                                              validate="email_validate")},
    )
    assert_parity(df, rules, compare_confidence=False)


def test_c1_groups_and_multiple_scalars_mixed():
    # The full Phase C surface: a group + several scalars under different
    # strategies, multiple clusters, ties, and an all-null scalar.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2, 2],
        "__row_id__": [10, 11, 20, 21, 22],
        "__source__": ["crm", "web", "web", "crm", "erp"],
        # group: lock-step address
        "street": ["1 A St", "2 B Ave", None, "3 C Rd", "3 C Rd Longer"],
        "city": ["LA", None, "SF", "SF", None],
        # scalars
        "name": ["Bob", "Robert Smith", "Al", None, "Alex"],   # most_complete (default)
        "phone": ["111", "222", "333", "444", "555"],          # source_priority
        "note": [None, None, "kept", None, None],              # first_non_null, mostly null
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", strategy="most_complete",
                                      columns=["street", "city"])],
        field_rules={
            "phone": GoldenFieldRule(strategy="source_priority",
                                     source_priority=["crm", "erp", "web"]),
            "note": GoldenFieldRule(strategy="first_non_null"),
        },
    )
    assert_parity(df, rules, compare_confidence=False)
