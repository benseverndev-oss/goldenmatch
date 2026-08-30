"""Polars-parity CSV writer for a ``pyarrow.Table`` -- the polars-free fallback.

``output/writer.py`` pins csv/xlsx byte formatting to the polars writers: that
is the published output contract, and changing it is a major-version decision
(see the note in ``write_output``). But polars is an OPTIONAL extra, so on a
plain ``pip install goldenmatch`` there is no polars writer to bridge to and
``goldenmatch dedupe data.csv --output-clusters`` could not write its output at
all.

This module writes the bytes polars writes. An install WITH polars still takes
the polars writer and is untouched; a polars-free install now gets the same
file instead of an error.

pyarrow's own ``write_csv`` cannot do this job: ``quoting_style="needed"``
still quotes every string value AND the entire header row, while ``"none"``
never quotes, which would corrupt any value containing the delimiter.

Formatting rules, each pinned against the real polars writer by
``tests/test_csv_arrow_polars_parity.py``:

  * a field is quoted only when it contains the delimiter, a double quote, CR
    or LF -- quotes inside are doubled;
  * a null writes as an EMPTY field while an empty string writes as ``""``;
    keeping those two distinguishable is why this is hand-rolled;
  * booleans write bare lowercase ``true`` / ``false``;
  * floats use ``repr`` (so ``2.0`` stays ``2.0``; pyarrow's writer emits
    ``2``), with bare ``NaN`` / ``inf`` / ``-inf``;
  * temporal values write ISO-8601 via ``isoformat()``.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _render(value: Any) -> str | None:
    """Cell text, or ``None`` for a SQL null (an empty, unquoted field)."""
    if value is None:
        return None
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _quote(text: str | None, delimiter: str) -> str:
    if text is None:
        return ""  # null -> empty field, never quoted
    if text == "":
        return '""'  # empty string -> quoted, so it is not read back as null
    if (
        delimiter in text
        or '"' in text
        or "\n" in text
        or "\r" in text
    ):
        return '"' + text.replace('"', '""') + '"'
    return text


def write_csv_polars_parity(
    table: Any, path: str | Path, *, delimiter: str = ","
) -> Path:
    """Write ``table`` (a ``pyarrow.Table``) to ``path`` the way polars would."""
    path = Path(path)
    names = list(table.column_names)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(delimiter.join(_quote(n, delimiter) for n in names))
        fh.write("\n")
        for batch in table.to_batches():
            for row in batch.to_pylist():
                fh.write(
                    delimiter.join(
                        _quote(_render(row[n]), delimiter) for n in names
                    )
                )
                fh.write("\n")
    return path
