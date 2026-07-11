"""Pure-Python reference implementation of goldencheck's OWN CSV type-inference
contract (polars-free).

This is deliberately NOT `pl.read_csv`'s inference -- it's a documented, simpler
contract that a later Rust kernel must agree with byte-for-byte. See
`docs/superpowers/plans/` (goldencheck CSV owned-inference wave) for the spec.

Contract (per column, over its non-empty cell values):
- `""` (empty string) is always null (`None`); nulls coexist with any inferred type.
- A column with zero non-empty values (all-empty) is `str` (all `None`).
- Otherwise, precedence int -> float -> bool -> str:
    1. int: every non-empty value matches `^-?[0-9]+$`, fits signed 64-bit, and is not
       a leading-zero multi-digit value (e.g. "01234", "-007"; "0"/"-0" ARE int).
    2. float: not all-int, but every non-empty value matches a finite decimal /
       scientific-notation regex, none is a leading-zero multi-digit value, and none
       is inf/nan (case-insensitive). The WHOLE column coerces to `float` (so "5"
       becomes `5.0` if any other value in the column needed float).
    3. bool: every non-empty value is true/false case-insensitive (not "0"/"1",
       not yes/no/t/f).
    4. str: anything else; values kept as-is.

Note on integers that don't fit signed 64-bit (e.g. "99999999999999999999"): they
match the int regex but fail the i64-bounds check, so int is rejected. They still
match the float regex (all digits, optional leading `-`), so they fall through to
float and get coerced with Python's arbitrary-precision-safe `float()` -- this is a
documented, deliberate consequence of the int -> float precedence, not a bug.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

_LEADING_ZERO = re.compile(r"^-?0[0-9]+$")
_INT = re.compile(r"^-?[0-9]+$")
_FLOAT = re.compile(r"^-?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$")
_BOOL_TRUE = "true"
_BOOL_FALSE = "false"

_I64_MIN = -9223372036854775808
_I64_MAX = 9223372036854775807


def _is_int(value: str) -> bool:
    if not _INT.match(value):
        return False
    if _LEADING_ZERO.match(value):
        return False
    return _I64_MIN <= int(value) <= _I64_MAX


def _is_float(value: str) -> bool:
    if not _FLOAT.match(value):
        return False
    if _LEADING_ZERO.match(value):
        return False
    lowered = value.lower().lstrip("-")
    if lowered in ("nan", "inf", "infinity"):
        return False
    return True


def _is_bool(value: str) -> bool:
    return value.lower() in (_BOOL_TRUE, _BOOL_FALSE)


def _infer_type(non_empty_values: list[str]) -> str:
    """Return one of "int", "float", "bool", "str" for a column's non-empty values."""
    if all(_is_int(v) for v in non_empty_values):
        return "int"
    if all(_is_float(v) for v in non_empty_values):
        return "float"
    if all(_is_bool(v) for v in non_empty_values):
        return "bool"
    return "str"


def _coerce(value: str, type_tag: str):
    if type_tag == "int":
        return int(value)
    if type_tag == "float":
        return float(value)
    if type_tag == "bool":
        return value.lower() == _BOOL_TRUE
    return value


def infer_and_type(cells: list[list[str]], header: list[str]) -> dict[str, list]:
    """Apply the owned CSV inference contract to pre-tokenized rows.

    `cells` is a list of rows, each row a list of string cells aligned to `header`.
    Returns `{column_name: [typed values]}` with `""` cells mapped to `None`.
    """
    columns: dict[str, list] = {name: [] for name in header}
    non_empty_by_col: dict[str, list[str]] = {name: [] for name in header}

    for row in cells:
        for col_idx, name in enumerate(header):
            raw = row[col_idx]
            if raw != "":
                non_empty_by_col[name].append(raw)

    type_by_col = {
        name: (_infer_type(values) if values else "str")
        for name, values in non_empty_by_col.items()
    }

    for row in cells:
        for col_idx, name in enumerate(header):
            raw = row[col_idx]
            if raw == "":
                columns[name].append(None)
            else:
                columns[name].append(_coerce(raw, type_by_col[name]))

    return columns


def _read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read a CSV file's rows via stdlib `csv`, trying utf-8 then latin-1."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
    except UnicodeDecodeError:
        with open(path, encoding="latin-1", newline="") as f:
            rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def read_csv_owned(path: str | Path) -> dict[str, list]:
    """Read a CSV file and apply the owned type-inference contract.

    First row is treated as the header. Reads via stdlib `csv` (utf-8, falling back
    to latin-1 on decode errors, mirroring `reader.py`'s Polars fallback pattern).
    """
    header, data_rows = _read_rows(Path(path))
    return infer_and_type(data_rows, header)
