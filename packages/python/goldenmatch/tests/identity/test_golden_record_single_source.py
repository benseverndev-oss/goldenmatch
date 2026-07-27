"""Identity golden-record rollup is single-sourced through core.golden.

Closes the thesis-conformance `three-golden-record-implementations` weakness:
`identity/resolve.py` used to hand-roll a "longest non-null string" rollup that
duplicated `core.golden`'s `most_complete` strategy. Both `_golden_record_from_*`
helpers now delegate to `core.golden.most_complete_value`. These tests pin the
delegation to BYTE-IDENTICAL output vs the retired inline rule, on adversarial
cases (ties, mixed types, empty strings, all-null, heterogeneous keys).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.golden import most_complete_value
from goldenmatch.identity.resolve import (
    _golden_record_from_members,
    _golden_record_from_payloads,
)


def _old_rule(non_null_values: list) -> object:
    """The retired inline rule: longest str-rep, ties broken by input order."""
    values = [(str(v), v) for v in non_null_values]
    values.sort(key=lambda x: len(x[0]), reverse=True)
    return values[0][1]


# Each case is a column's candidate values (as they arrive in member order).
_CASES = [
    ["Carol", "Caroline", "Car"],          # clear longest
    ["name", "n@me"],                       # same length -> first wins
    ["n@me", "name"],                       # same length, order flipped
    [None, "abc", "de"],                    # nulls ignored
    ["", "", ""],                           # all empty strings (not null)
    [123, "45"],                            # mixed types: str-rep length
    [1000000, 42],                          # ints: "1000000" longer
    ["only"],                               # singleton
    ["dup", "dup"],                         # identical values
    [None, None, "x"],                      # single non-null among nulls
]


@pytest.mark.parametrize("values", _CASES)
def test_most_complete_value_matches_retired_inline_rule(values):
    non_null = [v for v in values if v is not None]
    assert most_complete_value(values) == _old_rule(non_null)


def test_from_payloads_matches_old_rule_end_to_end():
    payloads = {
        0: {"name": "Bob", "city": "NYC", "note": None},
        1: {"name": "Robert", "city": "NYC", "note": "hi"},
        2: {"name": "Bo", "city": None, "note": None},
    }
    row_ids = [0, 1, 2]
    got = _golden_record_from_payloads(payloads, row_ids)

    # Reference: old rule per column, omit all-null columns, iterate members[0]'s keys.
    members = [payloads[r] for r in row_ids]
    expected = {}
    for col in members[0]:
        non_null = [m[col] for m in members if m.get(col) is not None]
        if not non_null:
            continue
        expected[col] = _old_rule(non_null)
    assert got == expected
    assert got == {"name": "Robert", "city": "NYC", "note": "hi"}


def test_from_members_matches_from_payloads():
    """The frame path and the payload path must agree (both = most_complete)."""
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2],
        "__source__": ["s", "s", "s"],
        "name": ["Bob", "Robert", "Bo"],
        "city": ["NYC", "NYC", None],
    })
    payloads = {
        0: {"name": "Bob", "city": "NYC"},
        1: {"name": "Robert", "city": "NYC"},
        2: {"name": "Bo", "city": None},
    }
    from_frame = _golden_record_from_members(df, [0, 1, 2])
    from_payload = _golden_record_from_payloads(payloads, [0, 1, 2])
    assert from_frame == from_payload == {"name": "Robert", "city": "NYC"}


def test_all_null_column_is_omitted():
    payloads = {0: {"a": None, "b": "x"}, 1: {"a": None, "b": "yy"}}
    got = _golden_record_from_payloads(payloads, [0, 1])
    assert "a" not in got  # all-null column dropped, not carried as None
    assert got == {"b": "yy"}


def test_empty_inputs():
    assert _golden_record_from_payloads({}, []) == {}
    assert _golden_record_from_payloads({0: {"a": "x"}}, [99]) == {}


def test_field_strategies_config_overrides_most_complete():
    """Config-aware survivorship (thesis low item): when a field_strategies config
    names a column, the identity golden path honors that GoldenFieldRule strategy
    via core.golden.merge_field instead of most_complete; unconfigured columns keep
    most_complete. Still ONE owner (core.golden), just config-driven per field."""
    from goldenmatch.config.schemas import GoldenFieldRule

    payloads = {
        0: {"name": "Bob", "city": "NYC"},
        1: {"name": "Robert", "city": "NYC"},
        2: {"name": "Bo", "city": None},
    }
    row_ids = [0, 1, 2]

    # No config -> most_complete: the LONGEST name wins.
    assert _golden_record_from_payloads(payloads, row_ids)["name"] == "Robert"

    # first_non_null config on `name` -> the FIRST member's name wins instead.
    fs = {"name": GoldenFieldRule(strategy="first_non_null")}
    got = _golden_record_from_payloads(payloads, row_ids, fs)
    assert got["name"] == "Bob"
    # An unconfigured column still uses the most_complete default.
    assert got["city"] == "NYC"

    # The frame path honors config identically.
    df = pl.DataFrame({
        "__row_id__": row_ids,
        "__source__": ["s", "s", "s"],
        "name": ["Bob", "Robert", "Bo"],
        "city": ["NYC", "NYC", None],
    })
    assert _golden_record_from_members(df, row_ids, fs)["name"] == "Bob"
