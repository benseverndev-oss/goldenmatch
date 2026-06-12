#!/usr/bin/env python3
"""Append the #855 parity cases to the TS port's parity_cases.json.

Adds one case per newly-ported module (fuzzy-values, approx-duplicate,
approx-fd, functional-dependency, composite-key). Record shapes mirror the
TS unit tests so the Python-generated goldens line up with TS scan output.

Run from the TS package dir (`packages/typescript/goldencheck`) so the relative
`tests/fixtures/...` path resolves to the goldens the TS parity test reads, then
run `gen_parity_goldens_js.py` from the same CWD to (re)generate the goldens.

Idempotent: re-running will not duplicate a case already present by name.
Ground rules (see docs/superpowers/plans/2026-06-11-855-goldencheck-ts-port.md):
string/int columns only, no nulls, no floats — so Python's CSV round-trip and
TS's TabularData agree on dtype and tuple distinctness.
"""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path("tests/fixtures/parity_cases.json")


def _case(name: str, description: str, records: list[dict]) -> dict:
    return {
        "name": name,
        "description": description,
        "input": {"kind": "records", "records": records},
        "options": {"sampleSize": 100000, "domain": None},
    }


def fuzzy_values_case() -> dict:
    variants = ["California", "Californa", "CALIFORNIA", "Texas", "New York"]
    clean = ["apple", "banana", "cherry"]
    records = [
        {"state": variants[i % len(variants)], "region": clean[i % len(clean)]}
        for i in range(120)
    ]
    return _case("fuzzy_values", "near-duplicate state encodings (California spellings)", records)


def approx_duplicate_case() -> dict:
    # 12 rows; rows 0 and 5 are byte-identical (Acme / NYC) -> one duplicate pair.
    rows = [
        ("Acme", "NYC"), ("Beta", "LA"), ("Gamma", "SF"), ("Delta", "CHI"),
        ("Epsilon", "BOS"), ("Acme", "NYC"), ("Zeta", "SEA"), ("Eta", "DEN"),
        ("Theta", "MIA"), ("Iota", "ATL"), ("Kappa", "PHX"), ("Lambda", "DAL"),
    ]
    records = [{"company": n, "city": c} for (n, c) in rows]
    return _case("approx_duplicate", "one exact duplicate row pair (Acme/NYC)", records)


def approx_fd_case() -> dict:
    records = [
        {"zip": i % 10, "city": f"city_{i % 10}", "amt": (i * 13) % 97}
        for i in range(300)
    ]
    for bad in (7, 50, 123):
        records[bad]["city"] = "WRONGCITY"
    return _case("approx_fd", "near-strict zip->city FD with 3 injected typos", records)


def functional_dependency_case() -> dict:
    zip_to_city = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
    records = [
        {"zip": i % 6, "city": zip_to_city[i % 6], "amt": (i * 7) % 50}
        for i in range(120)
    ]
    return _case("functional_dependency", "strict zip->city functional dependency", records)


def composite_key_case() -> dict:
    rows = [
        (1, 1, "a", 2), (1, 2, "b", 1), (1, 3, "c", 5),
        (2, 1, "a", 1), (2, 2, "d", 1), (3, 1, "e", 9),
    ]
    records = [
        {"order_id": o, "line_no": l, "sku": s, "qty": q} for (o, l, s, q) in rows
    ]
    return _case("composite_key", "(order_id, line_no) composite key; no single-col key", records)


NEW_CASES = [
    fuzzy_values_case(),
    approx_duplicate_case(),
    approx_fd_case(),
    functional_dependency_case(),
    composite_key_case(),
]


def main() -> None:
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"cases": []}
    existing = {c["name"] for c in manifest["cases"]}
    added = [c["name"] for c in NEW_CASES if c["name"] not in existing]
    manifest["cases"].extend(c for c in NEW_CASES if c["name"] not in existing)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"parity_cases.json: added {added or '(none — all present)'}; "
          f"total {len(manifest['cases'])} cases")


if __name__ == "__main__":
    main()
