from pathlib import Path
import polars as pl
import pytest
from goldencheck.engine.reader import read_file

FIXTURES = Path(__file__).parent.parent / "fixtures"

def test_read_csv():
    df = read_file(FIXTURES / "simple.csv")
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 5
    assert "email" in df.columns

def test_read_nonexistent():
    with pytest.raises(FileNotFoundError):
        read_file(Path("/nonexistent/file.csv"))

def test_read_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported file format"):
        read_file(Path("data.json"))

def test_read_empty_file(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(ValueError, match="no data rows"):
        read_file(empty)
