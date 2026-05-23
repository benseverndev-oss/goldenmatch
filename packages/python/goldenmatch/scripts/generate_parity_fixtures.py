"""Generate JSON parity test fixtures for the TS port.

Each fixture pins (inputs -> expected output) for one Python builtin
plugin. The TS port's vitest harness loads these fixtures and asserts
byte-equal output from its port.

Phase 5 Part 1 of N (closes goldenmatch issue #208 partial -- the 6
numeric plugins). Format-, business-, aggregation- builtins land in
follow-up PRs.

Run:

    .venv/Scripts/python.exe scripts/generate_parity_fixtures.py \
        --out packages/typescript/goldenmatch/tests/parity/fixtures/

Reproduces deterministically: same set of inputs every call. Used by
CI to refresh fixtures + the TS port's parity test loop.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from goldenmatch.plugins.builtin.numeric import (
    NumericMaxStrategy,
    NumericMeanStrategy,
    NumericMedianStrategy,
    NumericMinStrategy,
    NumericSumStrategy,
    NumericWeightedAverageStrategy,
)


def _serialize_result(result: tuple) -> dict:
    """Serialize a (value, conf, idx?) tuple to a JSON-friendly dict."""
    out: dict[str, Any] = {
        "value": result[0],
        "confidence": result[1],
    }
    if len(result) > 2:
        out["idx"] = result[2]
    else:
        out["idx"] = None
    return out


def _numeric_cases() -> list[dict]:
    """Curated input set covering happy path, ties, all-null, mixed
    numeric/non-numeric, and boolean exclusion."""
    return [
        {"id": "happy_path_distinct", "inputs": {"values": [10, 50, 25]}},
        {"id": "tied_max", "inputs": {"values": [50, 50, 10]}},
        {"id": "negative_numbers", "inputs": {"values": [-10, 0, 10]}},
        {"id": "single_value", "inputs": {"values": [42]}},
        {"id": "all_null", "inputs": {"values": [None, None]}},
        {"id": "mixed_with_strings",
         "inputs": {"values": [10, "abc", 30, None]}},
        {"id": "string_numbers", "inputs": {"values": ["10", "5", "20"]}},
        {"id": "boolean_excluded", "inputs": {"values": [True, 5, False, 10]}},
        {"id": "float_precision", "inputs": {"values": [0.1, 0.2, 0.3]}},
        {"id": "weighted_avg_with_weights",
         "inputs": {"values": [10, 20, 30],
                    "quality_weights": [1.0, 2.0, 3.0]}},
        {"id": "weighted_avg_uniform",
         "inputs": {"values": [10, 20, 30]}},
        {"id": "weighted_avg_zero_weight_excluded",
         "inputs": {"values": [10, 20, 30],
                    "quality_weights": [1.0, 0.0, 2.0]}},
    ]


def _emit_numeric_fixtures(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = _numeric_cases()
    strategies = [
        ("numeric_max", NumericMaxStrategy()),
        ("numeric_min", NumericMinStrategy()),
        ("numeric_mean", NumericMeanStrategy()),
        ("numeric_median", NumericMedianStrategy()),
        ("numeric_sum", NumericSumStrategy()),
        ("numeric_weighted_average", NumericWeightedAverageStrategy()),
    ]
    for name, strategy in strategies:
        fixture_cases = []
        for case in cases:
            inputs = case["inputs"]
            values = inputs["values"]
            kwargs = {k: v for k, v in inputs.items() if k != "values"}
            try:
                result = strategy.merge(values, **kwargs)
                expected = _serialize_result(result)
            except Exception as exc:
                expected = {"error": str(exc)}
            fixture_cases.append({
                "id": case["id"],
                "inputs": inputs,
                "expected": expected,
            })
        fixture = {
            "name": name,
            "schema_version": 1,
            "cases": fixture_cases,
        }
        out_path = out_dir / f"{name}.json"
        out_path.write_text(json.dumps(fixture, indent=2, default=str) + "\n")
        print(f"wrote {out_path} ({len(fixture_cases)} cases)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("packages/typescript/goldenmatch/tests/parity/fixtures"),
        help="Output directory for fixture JSON files",
    )
    args = ap.parse_args()
    _emit_numeric_fixtures(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
