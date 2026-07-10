"""Phase 4b guard: a covered config runs end-to-end with Polars NEVER imported.

Phases 2-3 built a Rust/native execution path for the owned transforms (string,
phonetic, nullable, numeric f64+i64, multi-output splits) on both the CSV file path
(``transform_file``) and the in-memory path. Phase 4b closes the in-memory path's
last Polars coupling: ``transform_columns_native`` runs a ``dict[str, list]`` frame
through ``Column.from_pylist`` -> owned kernels -> ``Column.to_pylist``, so a covered
config transforms data with Polars *not even imported*. This is the Layer-3 seam of
the Rust-is-the-reference thesis (Rust execution available with no Polars).

Run in a SUBPROCESS: an in-process check is meaningless once an earlier test imported
Polars.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(snippet: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_inmemory_native_transform_is_polars_free() -> None:
    """A string+numeric+split config over a dict frame runs with `polars not in
    sys.modules` the whole way through (or skips cleanly on a pre-0.25 wheel)."""
    result = _run(
        """
        import sys
        from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
        from goldenflow.engine import columnar
        from goldenflow.core._native_loader import native_module

        nm = native_module()
        if nm is None or not columnar.native_columns_ready(nm) \
           or not hasattr(nm, "columnar_split_ready"):
            print("SKIP")  # pre-4b wheel; the Polars engine still covers it
        else:
            assert "polars" not in sys.modules, "polars imported at setup"
            cfg = GoldenFlowConfig(transforms=[
                TransformSpec(column="name", ops=["strip", "lowercase"]),
                TransformSpec(column="price", ops=["currency_strip", "round:1"]),
                TransformSpec(column="addr", ops=["split_address"]),
            ])
            cols = {
                "name": ["  Hi ", "BYE", None],
                "price": ["$1,234.56", "$0.5", None],
                "addr": ["123 Main St, Reno, NV 89501", None, ""],
                "keep": [1, 2, 3],
            }
            out, man = columnar.transform_columns_native(cols, cfg)
            assert out["name"] == ["hi", "bye", None], out["name"]
            assert out["price"] == [1234.6, 0.5, None], out["price"]
            assert out["street"] == ["123 Main St", None, ""], out.get("street")
            assert "polars" not in sys.modules, "polars imported during execution"
            print("POLARS_FREE_OK")
        """
    )
    assert result in ("POLARS_FREE_OK", "SKIP"), result


def test_csv_file_transform_is_polars_free() -> None:
    """The CSV file path (`transform_file`) likewise runs with Polars not imported."""
    result = _run(
        """
        import sys, tempfile, os, csv
        from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
        from goldenflow.engine import columnar
        from goldenflow.core._native_loader import native_module

        nm = native_module()
        if nm is None or not hasattr(nm, "transform_csv"):
            print("SKIP")
        else:
            d = tempfile.mkdtemp()
            inp, out = os.path.join(d, "i.csv"), os.path.join(d, "o.csv")
            with open(inp, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["price", "keep"])
                for i, v in enumerate(["$1,234.56", "$0.5", "", None]):
                    w.writerow(["" if v is None else v, f"k{i}"])
            cfg = GoldenFlowConfig(transforms=[
                TransformSpec(column="price", ops=["currency_strip", "round:1"])
            ])
            columnar.transform_file(inp, out, cfg)
            assert "polars" not in sys.modules, "polars imported during CSV transform"
            print("POLARS_FREE_OK")
        """
    )
    assert result in ("POLARS_FREE_OK", "SKIP"), result
