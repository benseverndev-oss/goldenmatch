import polars as pl
import pytest
from goldencheck.core import kernels
from goldencheck.core._native_loader import native_available
from goldencheck.denial.evidence import (
    _evidence_python,
    _pair_evidence_oracle,
    _row_evidence_oracle,
    space_to_kernel_args,
)
from goldencheck.denial.predicates import build_predicate_space

native_only = pytest.mark.skipif(not native_available(), reason="native ext not built")


def _frame(seed):
    import random
    rng = random.Random(seed)
    return pl.DataFrame({
        "s": [rng.choice(["a", "b", None]) for _ in range(60)],
        "x": [rng.randint(1, 5) for _ in range(60)],
        "y": [rng.choice([1, 2, 3, None]) for _ in range(60)],
    })


@native_only
@pytest.mark.parametrize("seed", range(5))
def test_native_matches_python_and_oracle(seed, monkeypatch):
    df = _frame(seed)
    space = build_predicate_space(df)
    cols, nulls, spec, _ = space_to_kernel_args(space)
    sample = list(range(min(20, df.height)))

    for which, n, idx in [(1, df.height, []), (2, 0, sample)]:
        # pure-Python fallback (native OFF)
        monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
        py = kernels.denial_constraint_evidence(cols, nulls, spec, which, n, idx)
        # native (native ON)
        monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
        nat = kernels.denial_constraint_evidence(cols, nulls, spec, which, n, idx)
        assert nat == py, f"native != python, pass {which}, seed {seed}"
        # and both == the independent predicate_holds oracle
        oracle = _row_evidence_oracle(space, df.height) if which == 1 else _pair_evidence_oracle(space, sample)
        assert py == oracle, f"python != oracle, pass {which}, seed {seed}"


def test_evidence_python_direct_matches_oracle():
    # even without native, the cols-based _evidence_python must equal the predicate_holds oracle
    df = _frame(7)
    space = build_predicate_space(df)
    cols, nulls, spec, _ = space_to_kernel_args(space)
    assert _evidence_python(cols, nulls, spec, 1, df.height, []) == _row_evidence_oracle(space, df.height)
