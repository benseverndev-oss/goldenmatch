"""The runner must emit a valid fidelity tier for EVERY adapter row."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

VALID = {"real", "real-inproc", "real-live", "validated", "modeled"}


def test_every_row_has_valid_fidelity():
    from erkgbench import run  # pyright: ignore[reportMissingImports]
    report = run.run(None)  # offline; no key
    assert report["results"], "no adapter rows"
    for r in report["results"]:
        assert r.get("fidelity") in VALID, f"{r.get('name')!r} -> {r.get('fidelity')!r}"


def test_real_neo4j_row_present_and_real():
    from erkgbench import run  # pyright: ignore[reportMissingImports]
    report = run.run(None)
    real = [r for r in report["results"] if r["name"] == "neo4j-graphrag(fuzzy)*"]
    assert real, "real neo4j-graphrag row missing"
    assert real[0]["fidelity"] == "real-inproc"
    assert round(real[0]["overall"]["f1"], 3) == 0.470
