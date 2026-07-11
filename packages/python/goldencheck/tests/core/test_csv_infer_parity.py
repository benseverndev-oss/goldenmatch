"""Parity: the native `csv_infer_columns` kernel must produce the SAME typed
output as the pure-Python reference (`goldencheck.engine.csv_infer`) -- the
contract that reference module's docstring defines. This is the gate for
adding ``csv_infer`` to ``_native_loader._GATED_ON``.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import csv
import io
import random

import pytest
from goldencheck.core._native_loader import native_available, native_module
from goldencheck.engine.csv_infer import infer_and_type, read_csv_owned

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _cells_to_csv_bytes(header: list[str], cells: list[list[str]]) -> bytes:
    """Assemble well-formed CSV bytes from a header + cell matrix via the
    stdlib `csv` writer, so values containing commas/quotes/newlines are
    escaped the same way the Python reference's own reader expects."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(cells)
    return buf.getvalue().encode("utf-8")


def _native_infer(header: list[str], cells: list[list[str]]) -> dict:
    csv_bytes = _cells_to_csv_bytes(header, cells)
    return native_module().csv_infer_columns(csv_bytes, ord(","))


# ---------------------------------------------------------------------------
# Pinned edge cases from the CSV owned-inference contract spec.
# ---------------------------------------------------------------------------
PINNED_CASES: list[tuple[list[str], list[list[str]]]] = [
    # int
    (["a"], [["1"], ["2"], ["3"]]),
    (["a"], [["-1"], ["2"], ["-3"]]),
    (["a"], [["0"], ["1"]]),
    (["a"], [["-0"], ["1"]]),
    (["a"], [["9223372036854775807"], ["-9223372036854775808"]]),
    # leading-zero -> str
    (["z"], [["01234"], ["5"]]),
    (["z"], [["-007"], ["5"]]),
    # int overflow -> float
    (["a"], [["99999999999999999999"]]),
    # float
    (["f"], [["1"], ["2.5"]]),
    (["f"], [["1e10"], ["2.5"]]),
    (["f"], [[".5"], ["1.0"]]),
    (["f"], [["-3.5"], ["1"]]),
    # leading-zero WITH a dot stays float (guard is pure-digit only)
    (["f"], [["01.5"], ["2.5"]]),
    # nan/inf -> str
    (["x"], [["nan"], ["1.0"]]),
    (["x"], [["inf"], ["1.0"]]),
    (["x"], [["Infinity"], ["1.0"]]),
    (["x"], [["-inf"], ["1.0"]]),
    # trailing dot / leading plus -> str
    (["x"], [["5."], ["1.0"]]),
    (["x"], [["+5"], ["1.0"]]),
    # bool
    (["b"], [["true"], ["False"]]),
    (["b"], [["true"], ["True"], ["TRUE"], ["false"], ["False"], ["FALSE"]]),
    # 0/1 are int, not bool
    (["a"], [["0"], ["1"]]),
    # all-empty column -> str, all None
    (["a"], [[""], [""]]),
    # empty cells are null in every type
    (["a"], [["1"], [""], ["3"]]),
    (["f"], [["1.5"], [""], ["2.5"]]),
    (["b"], [["true"], [""], ["false"]]),
    (["z"], [["01234"], [""], ["5"]]),
    # mixed types -> str
    (["a"], [["1"], ["hello"]]),
    # multi-column
    (["a", "b", "c"], [["1", "true", "hello"], ["2", "false", "world"]]),
]


@native_only
@pytest.mark.parametrize("header,cells", PINNED_CASES)
def test_csv_infer_parity_pinned(header: list[str], cells: list[list[str]]) -> None:
    expected = infer_and_type(cells, header)
    actual = _native_infer(header, cells)
    assert actual == expected


# ---------------------------------------------------------------------------
# Random / fuzz cell matrices over a value pool that spans every type branch.
# ---------------------------------------------------------------------------
_VALUE_POOL = [
    "", "0", "-0", "1", "-1", "42", "-42", "007", "-007", "01234",
    "1.5", "-1.5", ".5", "-.5", "1e10", "1E-3", "01.5", "5.", "+5",
    "nan", "NaN", "inf", "-inf", "Infinity", "INFINITY",
    "true", "True", "TRUE", "false", "False", "FALSE",
    "hello", "world", "9223372036854775807", "-9223372036854775808",
    "99999999999999999999", "-99999999999999999999",
]


def _random_cell_matrix(seed: int, ncols: int = 3, nrows: int = 12) -> tuple[list[str], list[list[str]]]:
    rng = random.Random(seed)
    header = [f"col{i}" for i in range(ncols)]
    cells = [[rng.choice(_VALUE_POOL) for _ in range(ncols)] for _ in range(nrows)]
    return header, cells


@native_only
@pytest.mark.parametrize("seed", range(20))
def test_csv_infer_parity_random(seed: int) -> None:
    header, cells = _random_cell_matrix(seed)
    expected = infer_and_type(cells, header)
    actual = _native_infer(header, cells)
    assert actual == expected


# ---------------------------------------------------------------------------
# End-to-end: native reads raw CSV bytes itself, matching `read_csv_owned`.
# ---------------------------------------------------------------------------
@native_only
def test_read_csv_owned_end_to_end(tmp_path) -> None:
    content = (
        "id,name,score,active,note\n"
        "1,Alice,3.5,true,hello\n"
        "2,Bob,,,\n"
        "01234,Carol,nan,False,world\n"
    )
    path = tmp_path / "sample.csv"
    path.write_text(content, encoding="utf-8", newline="")

    expected = read_csv_owned(path)
    native_out = native_module().csv_infer_columns(content.encode("utf-8"), ord(","))
    assert native_out == expected


@native_only
def test_read_csv_owned_end_to_end_empty_file(tmp_path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8", newline="")

    expected = read_csv_owned(path)
    native_out = native_module().csv_infer_columns(b"", ord(","))
    assert native_out == expected == {}
