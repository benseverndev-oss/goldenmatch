"""Example 10 — Confidence calibration (new in v0.3)

Demonstrates the post-assignment calibration pipeline:

1. Run the engine uncalibrated to collect (confidence, correct) pairs
2. Fit an IsotonicCalibrator on those pairs
3. Re-run with the calibrator → confidences are now calibrated probabilities
4. Save/load the calibrator as JSON for reuse

The calibrator does NOT change which mappings are picked — only the
confidence score attached to each. F1, top-1, and MRR stay identical.

Run:
    python examples/10_calibration.py
"""
from __future__ import annotations

import json

from infermap import FieldInfo, MapEngine, SchemaInfo
from infermap.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    save_calibrator,
    load_calibrator,
)
import numpy as np


def make_schema(name: str, fields: list[tuple[str, str, list[str]]]) -> SchemaInfo:
    return SchemaInfo(
        fields=[
            FieldInfo(name=n, dtype=dt, sample_values=samples, value_count=len(samples))
            for n, dt, samples in fields
        ],
        source_name=name,
    )


def main() -> None:
    # A pair of schemas with known correct mappings
    source = make_schema("crm_export", [
        ("cust_id", "int64", ["1", "2", "3"]),
        ("e_mail", "string", ["a@x.io", "b@y.io", "c@z.io"]),
        ("amt", "float64", ["19.99", "42.00", "7.50"]),
    ])
    target = make_schema("warehouse", [
        ("customer_id", "int64", ["100", "200", "300"]),
        ("email", "string", ["x@a.io", "y@b.io", "z@c.io"]),
        ("amount_usd", "float64", ["12.34", "56.78", "9.10"]),
        ("notes", "string", ["", "", ""]),
    ])

    # Ground truth: what SHOULD map
    expected = {("cust_id", "customer_id"), ("e_mail", "email"), ("amt", "amount_usd")}

    # Step 1: Run uncalibrated
    engine = MapEngine()
    result = engine.map_schemas(source, target)
    print("=== Uncalibrated ===")
    for m in result.mappings:
        correct = (m.source, m.target) in expected
        print(f"  {m.source:>10} -> {m.target:<14} conf={m.confidence:.3f} {'OK' if correct else 'WRONG'}")

    # Step 2: Collect (confidence, correct) pairs for fitting
    scores = np.array([m.confidence for m in result.mappings])
    correct = np.array([1.0 if (m.source, m.target) in expected else 0.0 for m in result.mappings])

    # Step 3: Fit an isotonic calibrator
    cal = IsotonicCalibrator()
    cal.fit(scores, correct)
    print(f"\nFitted IsotonicCalibrator on {len(scores)} pairs")

    # Step 4: Re-run with calibrator
    cal_engine = MapEngine(calibrator=cal)
    cal_result = cal_engine.map_schemas(source, target)
    print("\n=== Calibrated ===")
    for m in cal_result.mappings:
        correct_flag = (m.source, m.target) in expected
        print(f"  {m.source:>10} -> {m.target:<14} conf={m.confidence:.3f} {'OK' if correct_flag else 'WRONG'}")

    # Verify invariant: same mappings, different confidences
    uncal_pairs = [(m.source, m.target) for m in result.mappings]
    cal_pairs = [(m.source, m.target) for m in cal_result.mappings]
    assert uncal_pairs == cal_pairs, "BUG: calibrator changed the mapping selection!"
    print("\nInvariant verified: mapping selection unchanged by calibration")

    # Step 5: Save and reload
    save_calibrator(cal, "calibrator.json")
    loaded = load_calibrator("calibrator.json")
    print(f"\nSaved and reloaded calibrator (kind={loaded.kind})")

    # Also demonstrate Platt
    platt = PlattCalibrator()
    platt.fit(scores, correct)
    print(f"Platt calibrator: a={platt.a:.3f}, b={platt.b:.3f}")

    # Cleanup
    import os
    os.remove("calibrator.json")
    print("\nDone! See docs/benchmark.md for using calibration with the benchmark runner.")


if __name__ == "__main__":
    main()
