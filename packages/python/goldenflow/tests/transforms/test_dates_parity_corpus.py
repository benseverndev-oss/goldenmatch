"""Byte-parity gate for the owned date kernel over the shared corpus.

Parametrizes over ``tests/parity/dates_corpus.jsonl`` -- the SAME file (committed
byte-for-byte in both the Python and TS packages; CI's ``goldenflow_wasm`` corpus
sync-check enforces they stay identical). Each row's ``iso``/``us``/``eu``/
``datetime`` are the expected outputs of the four owned string transforms.

This asserts the pure-Python scalars reproduce the corpus. The TS side asserts the
same corpus in ``tests/parity/dates.parity.test.ts``, and the WASM (built from
``goldenflow_core::dates``) is proven equal to pure-TS in the fused-chain parity
lane -- so one committed oracle pins all three surfaces to the same bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from goldenflow.transforms.dates import (
    _date_eu_py,
    _date_iso8601_py,
    _date_us_py,
    _datetime_iso8601_py,
)

_CORPUS = Path(__file__).parent.parent / "parity" / "dates_corpus.jsonl"


def _load() -> list[dict]:
    with _CORPUS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


_ROWS = _load()
_FIELD_FN = {
    "iso": _date_iso8601_py,
    "us": _date_us_py,
    "eu": _date_eu_py,
    "datetime": _datetime_iso8601_py,
}


@pytest.mark.parametrize("row", _ROWS, ids=[r["input"] or "<empty>" for r in _ROWS])
def test_date_scalars_match_corpus(row: dict) -> None:
    for field, fn in _FIELD_FN.items():
        assert fn(row["input"]) == row[field], f"{field} mismatch for {row['input']!r}"


def test_corpus_nonempty() -> None:
    # Guard against an empty/mis-pathed fixture silently passing the parametrize.
    assert len(_ROWS) >= 30
