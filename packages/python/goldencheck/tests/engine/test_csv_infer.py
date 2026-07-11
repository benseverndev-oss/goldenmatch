"""Tests for the pure-Python owned CSV type-inference contract (engine/csv_infer.py).

This is the CONTRACT REFERENCE that a later Rust kernel must agree with byte-for-byte.
Every example in the task spec is covered here plus additional edge cases.
"""

from __future__ import annotations

from goldencheck.engine.csv_infer import infer_and_type, read_csv_owned

# --- int -------------------------------------------------------------------


def test_int_column():
    assert infer_and_type([["1"], ["2"], ["3"]], ["a"]) == {"a": [1, 2, 3]}


def test_negative_int():
    assert infer_and_type([["-1"], ["2"], ["-3"]], ["a"]) == {"a": [-1, 2, -3]}


def test_leading_zero_stays_str():
    assert infer_and_type([["01234"], ["5"]], ["z"]) == {"z": ["01234", "5"]}


def test_negative_leading_zero_stays_str():
    assert infer_and_type([["-007"], ["5"]], ["z"]) == {"z": ["-007", "5"]}


def test_single_zero_is_int():
    assert infer_and_type([["0"], ["1"]], ["a"]) == {"a": [0, 1]}


def test_negative_zero_is_int():
    assert infer_and_type([["-0"], ["1"]], ["a"]) == {"a": [-0, 1]}


def test_int_bounds_i64_min_max():
    assert infer_and_type(
        [["9223372036854775807"], ["-9223372036854775808"]], ["a"]
    ) == {"a": [9223372036854775807, -9223372036854775808]}


def test_int_overflow_i64_falls_to_float():
    # All-digit but doesn't fit signed 64-bit -> falls through to float (matches the
    # float regex since it's all digits with an optional leading '-').
    assert infer_and_type([["99999999999999999999"]], ["a"]) == {
        "a": [99999999999999999999.0]
    }


# --- float -------------------------------------------------------------------


def test_float_coerces_whole_column():
    assert infer_and_type([["1"], ["2.5"]], ["f"]) == {"f": [1.0, 2.5]}


def test_float_scientific_notation():
    assert infer_and_type([["1e10"], ["2.5"]], ["f"]) == {"f": [1e10, 2.5]}


def test_float_leading_dot():
    assert infer_and_type([[".5"], ["1.0"]], ["f"]) == {"f": [0.5, 1.0]}


def test_float_negative():
    assert infer_and_type([["-3.5"], ["1"]], ["f"]) == {"f": [-3.5, 1.0]}


def test_float_with_decimal_point_leading_zero_is_float():
    # The leading-zero guard regex (`^-?0[0-9]+$`) only matches pure-digit strings,
    # so "01.5" (has a decimal point) is NOT blocked by it -- it's accepted as float.
    # This is a literal reading of the spec's "same guard as int" instruction; only
    # multi-digit *integer-shaped* values (no dot) are rejected as leading-zero.
    assert infer_and_type([["01.5"], ["2.5"]], ["f"]) == {"f": [1.5, 2.5]}


def test_nan_inf_stay_str():
    assert infer_and_type([["nan"], ["1.0"]], ["x"]) == {"x": ["nan", "1.0"]}
    assert infer_and_type([["inf"], ["1.0"]], ["x"]) == {"x": ["inf", "1.0"]}
    assert infer_and_type([["Infinity"], ["1.0"]], ["x"]) == {"x": ["Infinity", "1.0"]}
    assert infer_and_type([["-inf"], ["1.0"]], ["x"]) == {"x": ["-inf", "1.0"]}


def test_trailing_dot_and_plus_stay_str():
    assert infer_and_type([["5."], ["1.0"]], ["x"]) == {"x": ["5.", "1.0"]}
    assert infer_and_type([["+5"], ["1.0"]], ["x"]) == {"x": ["+5", "1.0"]}


# --- bool --------------------------------------------------------------------


def test_bool_column():
    assert infer_and_type([["true"], ["False"]], ["b"]) == {"b": [True, False]}


def test_bool_all_case_variants():
    assert infer_and_type(
        [["true"], ["True"], ["TRUE"], ["false"], ["False"], ["FALSE"]], ["b"]
    ) == {"b": [True, True, True, False, False, False]}


def test_zero_one_is_int_not_bool():
    assert infer_and_type([["0"], ["1"]], ["b"]) == {"b": [0, 1]}


def test_yes_no_t_f_stay_str():
    assert infer_and_type([["yes"], ["no"]], ["b"]) == {"b": ["yes", "no"]}
    assert infer_and_type([["t"], ["f"]], ["b"]) == {"b": ["t", "f"]}


# --- str / mixed / nulls ------------------------------------------------------


def test_mixed_is_str():
    assert infer_and_type([["1"], ["true"]], ["m"]) == {"m": ["1", "true"]}


def test_empty_is_null_coexists():
    assert infer_and_type([["1"], [""], ["3"]], ["a"]) == {"a": [1, None, 3]}
    assert infer_and_type([["a"], [""], ["c"]], ["s"]) == {"s": ["a", None, "c"]}


def test_empty_coexists_with_float_and_bool():
    assert infer_and_type([["1.5"], [""], ["2.5"]], ["f"]) == {"f": [1.5, None, 2.5]}
    assert infer_and_type([["true"], [""], ["false"]], ["b"]) == {
        "b": [True, None, False]
    }


def test_all_empty_is_str_all_none():
    assert infer_and_type([[""], [""]], ["e"]) == {"e": [None, None]}


def test_all_empty_single_row():
    assert infer_and_type([[""]], ["e"]) == {"e": [None]}


def test_plain_str_column():
    assert infer_and_type([["alice"], ["bob"]], ["name"]) == {"name": ["alice", "bob"]}


def test_multi_column():
    rows = [["1", "true", "hello"], ["2", "false", "world"]]
    assert infer_and_type(rows, ["a", "b", "c"]) == {
        "a": [1, 2],
        "b": [True, False],
        "c": ["hello", "world"],
    }


# --- read_csv_owned ------------------------------------------------------------


def test_read_csv_owned(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("id,zip,val,flag\n1,01234,1.5,true\n2,00099,2,false\n", encoding="utf-8")
    d = read_csv_owned(p)
    assert d == {
        "id": [1, 2],
        "zip": ["01234", "00099"],
        "val": [1.5, 2.0],
        "flag": [True, False],
    }


def test_read_csv_owned_empty_cells(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("a,b\n1,\n,x\n3,y\n", encoding="utf-8")
    d = read_csv_owned(p)
    assert d == {"a": [1, None, 3], "b": [None, "x", "y"]}


def test_read_csv_owned_latin1_fallback(tmp_path):
    p = tmp_path / "h.csv"
    # Write bytes that are valid latin-1 but NOT valid utf-8 (0xe9 = 'é' in latin-1,
    # invalid as a standalone utf-8 continuation byte).
    p.write_bytes(b"name,val\ncaf\xe9,1\n")
    d = read_csv_owned(p)
    assert d == {"name": ["caf\xe9"], "val": [1]}
