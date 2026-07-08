"""Cross-surface parity: Python profile_complexity == the shared vector
(which the TS profile-complexity-parity.test.ts also replays). Box-runnable."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from goldenpipe.autoconfig_glue import profile_complexity
from goldenpipe.models.context import PipeContext

# test is at packages/python/goldenpipe/tests/test_*.py
# parents: [0]=tests [1]=goldenpipe [2]=python [3]=packages [4]=REPO ROOT.
_VECTOR = (
    Path(__file__).resolve().parents[4]
    / "packages/rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json"
)


def _cases() -> list[dict]:
    data = json.loads(_VECTOR.read_text())
    return [c for c in data if "input" in c]  # skip the leading _comment entry


@pytest.mark.parametrize("case", _cases())
def test_profile_complexity_matches_vector(case: dict) -> None:
    rows = case["input"]["rows"]
    df = pl.DataFrame(rows)
    comp = profile_complexity(PipeContext(df=df))
    exp = case["expected"]
    assert comp.max_null_density == pytest.approx(exp["max_null_density"]), case.get("comment")
    assert comp.mean_null_density == pytest.approx(exp["mean_null_density"]), case.get("comment")
