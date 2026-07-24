"""Emit the cross-language anomaly-detection parity fixture.

Python is the ORACLE here: `core/anomaly.py` is the reference implementation and
`packages/typescript/goldenmatch/src/core/anomaly.ts` is the port. This script
runs the real Python detector over an adversarial row set and writes both the
inputs and the outputs so the TS parity test can assert byte-equality.

Usage:
    python scripts/emit_anomaly_fixture.py

Writes: packages/typescript/goldenmatch/tests/parity/fixtures/anomaly.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

from goldenmatch.core.anomaly import detect_anomalies

# Adversarial rows. Every column is a STRING -- leading-zero ZIPs ("00000") would
# otherwise be inferred as ints and lose the very shape under test, and
# str()/String() disagree on float spelling (the documented suite-wide divergence).
ROWS: list[dict] = [
    # -- fake emails, one per pattern family --------------------------------
    {"email": "test@foo.com", "phone": "212-555-0100", "zip": "02134", "note": "ok"},
    {"email": "noreply@corp.com", "phone": "212-555-0101", "zip": "02135", "note": "ok"},
    {"email": "someone@example.com", "phone": "212-555-0102", "zip": "02136", "note": "ok"},
    {"email": "aaa@corp.com", "phone": "212-555-0103", "zip": "02137", "note": "ok"},
    {"email": "qwerty@corp.com", "phone": "212-555-0104", "zip": "02138", "note": "ok"},
    # -- fake phones --------------------------------------------------------
    {"email": "real.person@corp.com", "phone": "555-0199", "zip": "02139", "note": "ok"},
    {"email": "other.person@corp.com", "phone": "000-1234", "zip": "02140", "note": "ok"},
    {"email": "third.person@corp.com", "phone": "11111222", "zip": "02141", "note": "ok"},
    # -- suspicious zips ----------------------------------------------------
    {"email": "zip.one@corp.com", "phone": "212-555-0105", "zip": "00000", "note": "ok"},
    {"email": "zip.two@corp.com", "phone": "212-555-0106", "zip": "99999", "note": "ok"},
    # -- placeholders (original casing must survive into `value`) -----------
    {"email": "ph.one@corp.com", "phone": "212-555-0107", "zip": "02142", "note": "TBD"},
    {"email": "ph.two@corp.com", "phone": "212-555-0108", "zip": "02143", "note": "N/A"},
    {"email": "ph.three@corp.com", "phone": "212-555-0109", "zip": "02144", "note": "  Unknown  "},
    # -- nulls are skipped entirely -----------------------------------------
    {"email": None, "phone": None, "zip": None, "note": None},
    # -- EXACTLY TWO identical rows: must NOT flag (the >2 boundary) --------
    {"email": "pair@corp.com", "phone": "212-555-0110", "zip": "02145", "note": "ok"},
    {"email": "pair@corp.com", "phone": "212-555-0110", "zip": "02145", "note": "ok"},
    # -- THREE identical rows: must flag all three --------------------------
    {"email": "trip@corp.com", "phone": "212-555-0111", "zip": "02146", "note": "ok"},
    {"email": "trip@corp.com", "phone": "212-555-0111", "zip": "02146", "note": "ok"},
    {"email": "trip@corp.com", "phone": "212-555-0111", "zip": "02146", "note": "ok"},
]

# A second frame carrying an explicit __row_id__, which must be preferred over
# the positional index (and must NOT itself be scanned for placeholders).
ROWS_WITH_ROW_ID: list[dict] = [
    {"__row_id__": 100, "email": "test@a.com", "note": "ok"},
    {"__row_id__": 200, "email": "fine@a.com", "note": "xxx"},
]


def _table(rows: list[dict]) -> pa.Table:
    cols = list(rows[0].keys())
    return pa.table({c: [r.get(c) for r in rows] for c in cols})


def main() -> None:
    cases = []
    for sensitivity in ("low", "medium", "high"):
        cases.append(
            {
                "name": f"adversarial_{sensitivity}",
                "rows": ROWS,
                "sensitivity": sensitivity,
                "expected": detect_anomalies(_table(ROWS), sensitivity),
            }
        )
    cases.append(
        {
            "name": "explicit_row_id",
            "rows": ROWS_WITH_ROW_ID,
            "sensitivity": "high",
            "expected": detect_anomalies(_table(ROWS_WITH_ROW_ID), "high"),
        }
    )

    out = (
        Path(__file__).resolve().parents[3]
        / "typescript"
        / "goldenmatch"
        / "tests"
        / "parity"
        / "fixtures"
        / "anomaly.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"cases": cases}, indent=2) + "\n", encoding="utf-8")
    total = sum(len(c["expected"]) for c in cases)
    print(f"wrote {out} ({len(cases)} cases, {total} anomalies)")


if __name__ == "__main__":
    main()
