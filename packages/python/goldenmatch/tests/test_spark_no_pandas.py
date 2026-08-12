"""The Spark tier must not depend on pandas.

This repo evicted polars, made pyarrow the hard dependency, and does not declare
pandas anywhere. The Spark tier nevertheless used `pandas_udf` throughout, which
made pandas a RUNTIME REQUIREMENT ON EVERY EXECUTOR -- and the environment P1
ships had to `pip install pandas` explicitly to make the tier work. An undeclared
dependency, documented rather than fixed.

Spark has first-class Arrow UDFs (`arrow_udf`, `mapInArrow`, `applyInArrow`), so
there was never a need for the pandas API.

These tests are the ratchet. Without them the dependency returns the first time
somebody reaches for `pandas_udf` out of habit, and nothing notices -- the
shipped environment simply grows.

No Spark needed: this reads the tier's source.
"""
from __future__ import annotations

import pathlib

import pytest

_SPARK_PKG = (
    pathlib.Path(__file__).resolve().parents[1] / "goldenmatch" / "spark"
)

#: Source files of the tier. `_arrow.py` is included -- it is the module that
#: REPLACED pandas and must not smuggle it back in a fallback.
_MODULES = sorted(p for p in _SPARK_PKG.glob("*.py"))


def _code_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """Lines with comments stripped, so prose ABOUT pandas does not trip the
    gate. The history of this change is worth recording in comments; what must
    not come back is the dependency."""
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0]
        out.append((i, line))
    return out


def test_the_tier_has_source_files():
    """A glob that matched nothing would make every test below vacuous."""
    assert len(_MODULES) >= 8, f"only found {len(_MODULES)} modules in {_SPARK_PKG}"


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
def test_no_pandas_udf(path):
    """`pandas_udf` is the API that drags pandas onto executors."""
    # `pandas_udf(` -- a CALL or decorator, not the bare word. `_arrow.py`'s
    # docstring names the API it replaced, and that record is worth keeping;
    # what must not come back is the usage.
    hits = [(i, ln.strip()) for i, ln in _code_lines(path) if "pandas_udf(" in ln]
    assert not hits, (
        f"{path.name} uses pandas_udf at {hits}. Use `arrow_udf` from "
        f"goldenmatch.spark._arrow: same shape, pa.Array instead of pd.Series, "
        f"and pyarrow is already a hard dependency."
    )


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
def test_no_pandas_import(path):
    """Importing pandas anywhere in the tier puts it back in the shipped env."""
    hits = [
        (i, ln.strip())
        for i, ln in _code_lines(path)
        if "import pandas" in ln or "import pandas as pd" in ln
    ]
    assert not hits, f"{path.name} imports pandas at {hits}"


def test_the_arrow_helper_does_not_fall_back_to_pandas():
    """A fallback would be worse than the original problem: it would work in
    test and quietly reinstate the dependency in production. `arrow_udf` must
    RAISE when pyspark cannot provide it."""
    src = (_SPARK_PKG / "_arrow.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "pandas_udf(" not in code, "_arrow.py falls back to pandas_udf"
    assert "raise RuntimeError" in code, (
        "_arrow.py must raise when arrow_udf is unavailable, not degrade"
    )


def test_pandas_is_not_a_declared_dependency():
    """The premise of all of the above. If pandas were ever added to the
    package's dependencies this gate would be arguing against the project's own
    manifest, and someone should reconsider deliberately rather than by import.
    """
    pyproject = (
        pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text(encoding="utf-8")
    lines = [
        ln for ln in pyproject.splitlines()
        if "pandas" in ln and not ln.strip().startswith("#")
    ]
    assert not lines, f"pandas appears in pyproject.toml: {lines}"
