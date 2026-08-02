"""Emit the cross-surface golden for the structural key-integrity kernel.

`certify_key_integrity` (structural tier) is the REFERENCE; this authors the
oracle `key-integrity-core/golden/key_integrity_golden.json` that:
  - the Rust kernel asserts (`key-integrity-core/tests/golden.rs`), and
  - the TS/WASM surface asserts (`tests/parity/fixtures/key-integrity/…`, copied by
    `scripts/build_key_integrity_wasm.mjs`).

Each case carries the KERNEL INPUT (`n_rows`, `group_columns` = the declared key
columns, or key+grain under grain_strict; `measures` = the numeric measures) and
the EXPECTED structural output read off the real Python certificate. A green run
on all three surfaces proves the group-by uniqueness + fan-out reduction is
identical everywhere.

`semantic/key_integrity.py`'s STRUCTURAL path imports only `pyarrow` +
`core/key_integrity_certificate.py`, so we load it in isolation rather than
importing the whole toolkit.

Run: `python3 scripts/emit_key_integrity_golden.py`
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pyarrow as pa

_PKG = Path(__file__).resolve().parents[1] / "goldenmatch"
_OUT = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "extensions"
    / "key-integrity-core"
    / "golden"
    / "key_integrity_golden.json"
)


def _load(name: str, relpath: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PKG / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:  # normal path
    from goldenmatch.semantic.key_integrity import certify_key_integrity
except ImportError:  # isolated loader
    sys.modules.setdefault("goldenmatch", types.ModuleType("goldenmatch"))
    sys.modules.setdefault("goldenmatch.semantic", types.ModuleType("goldenmatch.semantic"))
    sys.modules.setdefault("goldenmatch.core", types.ModuleType("goldenmatch.core"))
    _load("goldenmatch.core.key_integrity_certificate", "core/key_integrity_certificate.py")
    _ki = _load("goldenmatch.semantic.key_integrity", "semantic/key_integrity.py")
    certify_key_integrity = _ki.certify_key_integrity


def _case(name, table, *, key, measures=None, grain=None, grain_strict=False):
    cert = certify_key_integrity(
        table, key=key, measures=measures, grain=grain, grain_strict=grain_strict
    )
    key_cols = [key] if isinstance(key, str) else list(key)
    grain_cols = [] if grain is None else ([grain] if isinstance(grain, str) else list(grain))
    if grain_strict and grain_cols:
        group_cols = key_cols + [g for g in grain_cols if g not in key_cols]
    else:
        group_cols = key_cols
    measure_cols = [] if measures is None else list(measures)
    return {
        "name": name,
        "input": {
            "n_rows": table.num_rows,
            "group_columns": [table.column(c).to_pylist() for c in group_cols],
            "measures": [
                {"name": m, "values": table.column(m).to_pylist()} for m in measure_cols
            ],
        },
        "expected": {
            "n_key_groups": cert.n_key_groups,
            "duplicate_key_groups": cert.duplicate_key_groups,
            "max_fan_out": cert.max_fan_out,
            "is_unique_at_grain": cert.is_unique_at_grain,
            "measure_fan_out": cert.measure_fan_out,
        },
    }


def _cases():
    cases = []

    # 1. Unique key, no measures.
    cases.append(
        _case(
            "unique_key",
            pa.table({"id": ["e1", "e2", "e3"], "amt": [10, 20, 30]}),
            key="id",
        )
    )
    # 2. Duplicated key + a numeric measure (fan-out on key + measure).
    cases.append(
        _case(
            "duplicated_key_measure",
            pa.table({"id": ["e1", "e1", "e2", "e3"], "amt": [10.0, 30.0, 8.0, 40.0]}),
            key="id",
            measures=["amt"],
        )
    )
    # 3. Compound key.
    cases.append(
        _case(
            "compound_key",
            pa.table(
                {
                    "src": ["a", "a", "a", "b"],
                    "pk": [1, 1, 2, 1],
                    "rev": [5.0, 15.0, 3.0, 9.0],
                }
            ),
            key=["src", "pk"],
            measures=["rev"],
        )
    )
    # 4. Integer key with duplicates (int grouping).
    cases.append(
        _case(
            "int_key_dupes",
            pa.table({"k": [1, 1, 2, 2, 2], "v": [2, 4, 6, 6, 12]}),
            key="k",
            measures=["v"],
        )
    )
    # 5. grain_strict: key legitimately repeats across a grain bucket.
    cases.append(
        _case(
            "grain_strict",
            pa.table(
                {
                    "customer_id": [1, 1, 2, 2],
                    "day": ["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"],
                    "spend": [10.0, 20.0, 5.0, 7.0],
                }
            ),
            key="customer_id",
            measures=["spend"],
            grain=["day"],
            grain_strict=True,
        )
    )
    # 6. Measure with nulls (null values excluded from both sums).
    cases.append(
        _case(
            "measure_with_nulls",
            pa.table({"id": ["e1", "e1", "e2"], "amt": [10.0, None, 4.0]}),
            key="id",
            measures=["amt"],
        )
    )
    # 7. Empty frame.
    cases.append(
        _case(
            "empty",
            pa.table({"id": pa.array([], type=pa.string())}),
            key="id",
        )
    )
    return cases


def main() -> None:
    payload = {
        "_comment": (
            "Generated by scripts/emit_key_integrity_golden.py from the Python "
            "reference certify_key_integrity (structural tier). Oracle for the Rust "
            "kernel (key-integrity-core) + the TS/WASM surface. Do not hand-edit."
        ),
        "cases": _cases(),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['cases'])} cases -> {_OUT}")


if __name__ == "__main__":
    main()
