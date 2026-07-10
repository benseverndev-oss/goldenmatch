"""Zero-gap Wave 1: the caller-data categorical transforms on the columnar path.

- ``category_standardize`` takes a variant->canonical dict only via the direct API
  (there is no config/ops-string channel for a dict), so through the config-driven
  columnar path it is a no-op — identical to the Polars engine's ``mapping=None ->
  return series``. Wired as an identity scalar.
- ``category_from_file:<path>`` loads the mapping from a CSV/YAML file; wired as a
  ``scalar_factory`` that reads the file Polars-free (stdlib csv/yaml) once and applies
  ``mapping.get(trim+lower key, original)`` per element — byte-identical to the engine.
"""
from __future__ import annotations

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _cfg(specs):
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _mrows(m):
    return [
        (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
         tuple(r.sample_after or [])) for r in m.records
    ]


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


def _write_map_csv(path):
    path.write_text(
        "variant,canonical\nnyc,New York\nla,Los Angeles\nsf,San Francisco\n",
        encoding="utf-8",
    )


def _write_map_yaml(path):
    path.write_text(
        "New York:\n  - nyc\n  - ny\nLos Angeles:\n  - la\n", encoding="utf-8"
    )


DATA = {"c": ["NYC", " la ", "SF", "Boston", None], "k": [0, 1, 2, 3, 4]}


def test_category_standardize_identity_columnar() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("c", ["category_standardize"])])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dict(DATA), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(DATA), config=cfg)
    assert res.columns["c"] == ref.df["c"].to_list()
    assert _mrows(res.manifest) == _mrows(ref.manifest)


@pytest.mark.parametrize("ext,writer", [("csv", _write_map_csv), ("yaml", _write_map_yaml)])
def test_category_from_file_columnar_equals_polars(monkeypatch, tmp_path, ext, writer) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    # relative path: the ':' op-delimiter would split a Windows drive path (engine and
    # columnar mangle it identically, but a relative name avoids the ambiguity entirely)
    monkeypatch.chdir(tmp_path)
    writer(tmp_path / f"map.{ext}")
    for ops in ([f"category_from_file:map.{ext}"], ["strip", f"category_from_file:map.{ext}"]):
        cfg = _cfg([("c", ops)])
        assert columnar.config_is_columnar_ready(cfg)
        res = goldenflow.transform(dict(DATA), config=cfg)
        ref = goldenflow.transform_df(pl.DataFrame(DATA), config=cfg)
        assert res.columns["c"] == ref.df["c"].to_list(), ops
        assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_category_from_file_no_path_is_identity() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("c", ["category_from_file"])])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dict(DATA), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(DATA), config=cfg)
    assert res.columns["c"] == ref.df["c"].to_list()


def test_category_from_file_columnar_is_polars_free(monkeypatch, tmp_path) -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native in-memory core not built")
    _write_map_csv(tmp_path / "map.csv")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, os, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            os.chdir(sys.argv[1])
            cfg = GoldenFlowConfig(transforms=[
                TransformSpec(column="c", ops=["category_from_file:map.csv"]),
            ])
            res = goldenflow.transform({"c": ["NYC", "la", None], "k": [1, 2, 3]}, config=cfg)
            assert res.columns["c"] == ["New York", "Los Angeles", None], res.columns["c"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        ), str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
