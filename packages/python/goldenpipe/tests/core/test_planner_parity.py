"""SP2 Leg A: the pure-Python planner (via _planner_json) must reproduce the SP1
golden vectors (the core's output) byte-for-byte. Box-safe, no wheel."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from goldenpipe.core import _planner_json as PJ

# repo-relative: test file is packages/python/goldenpipe/tests/core/test_planner_parity.py
# parents: [0]=core [1]=tests [2]=goldenpipe [3]=python [4]=packages [5]=REPO ROOT.
_VECTORS = Path(__file__).resolve().parents[5] / "packages/rust/extensions/goldenpipe-core/tests/vectors"


def _load(name: str) -> list[dict]:
    return json.loads((_VECTORS / f"{name}.json").read_text())


_CASES = [
    ("resolve", PJ.resolve_json),
    ("apply_decision", PJ.apply_decision_json),
    ("evaluate_builtin", PJ.evaluate_builtin_json),
    ("auto_config", PJ.auto_config_json),
    ("skip_if", PJ.skip_if_falsy_json),
    ("plan_pipeline", PJ.plan_pipeline_json),
    ("apply_scale_hints", PJ.apply_scale_hints_json),
    ("band_of", PJ.band_of_json),
]


@pytest.mark.parametrize("name,fn", _CASES)
def test_pure_python_matches_core_vectors(name, fn):
    for i, case in enumerate(_load(name)):
        got = json.loads(fn(json.dumps(case["input"])))
        assert got == case["expected"], f"{name}[{i}] input={case['input']!r}"


# --- Leg B: the native wheel reproduces the core (CI-primary; skip-guarded locally) ---
from goldenpipe.core import _native_loader as NL  # noqa: E402


@pytest.mark.parametrize(
    "name,fn_name",
    [
        ("resolve", "resolve_json"),
        ("apply_decision", "apply_decision_json"),
        ("evaluate_builtin", "evaluate_builtin_json"),
        ("auto_config", "auto_config_json"),
        ("skip_if", "skip_if_falsy_json"),
        ("plan_pipeline", "plan_pipeline_json"),
        ("apply_scale_hints", "apply_scale_hints_json"),
        ("band_of", "band_of_json"),
    ],
)
def test_native_wheel_matches_core_vectors(name, fn_name):
    import os

    if not NL.native_available():
        if os.environ.get("GOLDENPIPE_NATIVE") == "1":
            pytest.fail("GOLDENPIPE_NATIVE=1 but the native wheel is not importable")
        pytest.skip("native wheel not built (Leg B is CI-primary)")
    fn = getattr(NL, fn_name)
    for i, case in enumerate(_load(name)):
        got = json.loads(fn(json.dumps(case["input"])))
        assert got == case["expected"], f"native {name}[{i}] input={case['input']!r}"
