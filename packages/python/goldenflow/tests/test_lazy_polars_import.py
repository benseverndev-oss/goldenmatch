"""Phase 4a guard: ``import goldenflow`` must NOT eagerly import Polars.

The transform registry pulls every transform module at ``import goldenflow`` time,
so a single top-level ``import polars`` anywhere in that chain loaded Polars for
every user -- including those who only touch the Polars-free columnar path. Phase 4a
routes all those modules through the lazy proxy (``goldenflow._polars_lazy``), so
Polars loads only on first actual use. This test locks that in: a regression (a new
top-level ``import polars as pl`` or a module-level ``pl.<attr>`` reference) would
re-import Polars at load time and fail here.

Run in a SUBPROCESS so it's a clean interpreter -- an in-process check is meaningless
once any earlier test has imported Polars.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def _fresh_import_check(snippet: str) -> str:
    """Run `snippet` in a clean interpreter and return its stdout (stripped)."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_import_goldenflow_does_not_load_polars() -> None:
    result = _fresh_import_check(
        """
        import sys
        import goldenflow  # noqa: F401
        print("LOADED" if "polars" in sys.modules else "CLEAN")
        """
    )
    assert result == "CLEAN", (
        "import goldenflow eagerly imported Polars -- a module in the registry chain "
        "has a top-level `import polars as pl` or a module-level `pl.<attr>` "
        "reference. Route it through goldenflow._polars_lazy (Phase 4a)."
    )


def test_polars_loads_on_first_actual_use() -> None:
    """The lazy proxy is transparent: a real transform still works (Polars loads on
    first use), so the eviction of the EAGER import changes no behavior."""
    result = _fresh_import_check(
        """
        import goldenflow
        import polars as pl
        from goldenflow import transform_df
        from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
        cfg = GoldenFlowConfig(transforms=[TransformSpec(column="x", ops=["strip", "lowercase"])])
        out = transform_df(pl.DataFrame({"x": ["  Hi ", "BYE"]}), config=cfg)
        print(",".join(out.df["x"].to_list()))
        """
    )
    assert result == "hi,bye"
