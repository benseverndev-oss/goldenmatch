from pathlib import Path

import polars as pl

from goldenflow.connectors.file import read_file, write_file


def test_read_csv(sample_csv: Path):
    df = read_file(sample_csv)
    assert isinstance(df, pl.DataFrame)
    assert df.shape[0] == 3  # sample_csv fixture has 3 rows
    assert "name" in df.columns


def test_read_parquet(tmp_path: Path):
    path = tmp_path / "data.parquet"
    pl.DataFrame({"a": [1, 2, 3]}).write_parquet(path)
    df = read_file(path)
    assert df.shape == (3, 1)


def test_read_json(tmp_path: Path):
    path = tmp_path / "data.json"
    pl.DataFrame({"a": [1, 2, 3]}).write_json(path)
    df = read_file(path)
    assert df.shape == (3, 1)


def test_write_csv(tmp_path: Path):
    df = pl.DataFrame({"a": [1, 2]})
    out = tmp_path / "out.csv"
    write_file(df, out)
    assert out.exists()
    result = pl.read_csv(out)
    assert result.shape == (2, 1)


def test_write_parquet(tmp_path: Path):
    df = pl.DataFrame({"a": [1, 2]})
    out = tmp_path / "out.parquet"
    write_file(df, out)
    assert out.exists()


def test_unsupported_format(tmp_path: Path):
    path = tmp_path / "data.xyz"
    path.write_text("hello")
    import pytest

    with pytest.raises(ValueError, match="Unsupported file format"):
        read_file(path)
