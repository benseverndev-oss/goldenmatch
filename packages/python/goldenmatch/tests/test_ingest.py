"""Tests for goldenmatch file ingest."""

from pathlib import Path

import polars as pl
import pytest
from goldenmatch.core.ingest import load_file, load_files, validate_columns


class TestLoadFile:
    """Tests for the load_file function."""

    def test_load_csv(self, sample_csv):
        lf = load_file(sample_csv)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert len(df) == 5
        assert "first_name" in df.columns

    def test_load_csv_with_delimiter(self, tmp_path):
        path = tmp_path / "tab.csv"
        df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        df.write_csv(path, separator="\t")
        lf = load_file(path, delimiter="\t")
        result = lf.collect()
        assert len(result) == 2
        assert result.columns == ["a", "b"]

    def test_load_parquet(self, sample_parquet):
        lf = load_file(sample_parquet)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert len(df) == 3
        assert "first_name" in df.columns

    def test_load_excel(self, tmp_path):
        pytest.importorskip("xlsxwriter")
        path = tmp_path / "test.xlsx"
        df = pl.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})
        df.write_excel(path)
        lf = load_file(path)
        assert isinstance(lf, pl.LazyFrame)
        result = lf.collect()
        assert len(result) == 3
        assert "name" in result.columns

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_file(Path("/nonexistent/file.csv"))

    def test_unsupported_format(self, tmp_path):
        path = tmp_path / "data.json"
        path.write_text('{"a": 1}')
        with pytest.raises(ValueError, match="Unsupported"):
            load_file(path)


class TestArrowRouteEligibleMirrorsDispatch:
    """``_arrow_route_eligible``'s docstring claims it "Mirrors the exact
    dispatch below" in ``load_file``: for every (suffix, parse_mode,
    delimiter) combination, its verdict must match whether ``load_file``
    actually takes the pyarrow route (vs. falling through to
    ``smart_load``/the polars-native suffix branches) once the arrow frame
    backend is selected."""

    @pytest.mark.parametrize(
        "suffix,parse_mode,delimiter",
        [
            (".parquet", "auto", None),
            (".parquet", "fixed_width", None),  # suffix always wins over parse_mode
            (".xlsx", "auto", None),
            (".xlsx", "block", None),
            (".csv", "auto", None),  # suffix == ".csv" -> eligible even with no delimiter
            (".csv", "auto", ";"),
            (".csv", "fixed_width", None),  # non-auto parse_mode -> smart_load
            (".txt", "auto", None),  # non-.csv text, no delimiter -> smart_load
            (".txt", "auto", "|"),  # non-.csv text, explicit delimiter -> eligible
            (".txt", "key_value", "|"),  # non-auto parse_mode -> smart_load regardless
            ("", "auto", None),  # no suffix, no delimiter -> smart_load
            ("", "auto", ","),  # no suffix, explicit delimiter -> eligible
        ],
    )
    def test_eligibility_matches_actual_route_taken(
        self, tmp_path, monkeypatch, suffix, parse_mode, delimiter
    ):
        from goldenmatch.core.ingest import _arrow_route_eligible

        expected = _arrow_route_eligible(suffix, parse_mode, delimiter)

        monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")

        path = tmp_path / f"data{suffix}"
        if suffix == ".parquet":
            pl.DataFrame({"a": [1, 2]}).write_parquet(path)
        elif suffix == ".xlsx":
            pytest.importorskip("xlsxwriter")
            pl.DataFrame({"a": [1, 2]}).write_excel(path)
        else:
            path.write_text("a,b\n1,2\n", encoding="utf-8")

        took_arrow_route = {"value": False}

        def fake_read_table_arrow(*args, **kwargs):
            took_arrow_route["value"] = True
            import pyarrow as pa

            return pa.table({"a": [1, 2]})

        monkeypatch.setattr(
            "goldenmatch.core.io_arrow.read_table_arrow", fake_read_table_arrow
        )

        load_file(path, parse_mode=parse_mode, delimiter=delimiter)

        assert took_arrow_route["value"] == expected, (
            f"_arrow_route_eligible({suffix!r}, {parse_mode!r}, {delimiter!r}) "
            f"= {expected} but load_file actually took the arrow route: "
            f"{took_arrow_route['value']}"
        )


class TestLoadFiles:
    """Tests for the load_files function."""

    def test_multi_file_loading(self, sample_csv, sample_csv_b):
        specs = [(sample_csv, "source_a"), (sample_csv_b, "source_b")]
        frames = load_files(specs)
        assert len(frames) == 2

        df_a = frames[0].collect()
        df_b = frames[1].collect()
        assert "__source__" in df_a.columns
        assert "__source__" in df_b.columns
        assert df_a["__source__"].unique().to_list() == ["source_a"]
        assert df_b["__source__"].unique().to_list() == ["source_b"]

    def test_single_file_loading(self, sample_csv):
        specs = [(sample_csv, "only_source")]
        frames = load_files(specs)
        assert len(frames) == 1
        df = frames[0].collect()
        assert df["__source__"].unique().to_list() == ["only_source"]


class TestValidateColumns:
    """Tests for the validate_columns function."""

    def test_valid_columns(self, sample_csv):
        lf = load_file(sample_csv)
        # Should not raise
        validate_columns(lf, ["id", "first_name", "last_name"])

    def test_missing_columns(self, sample_csv):
        lf = load_file(sample_csv)
        with pytest.raises(ValueError, match="Missing required columns"):
            validate_columns(lf, ["id", "nonexistent_column", "another_missing"])

    def test_missing_columns_lists_available(self, sample_csv):
        lf = load_file(sample_csv)
        with pytest.raises(ValueError, match="Available columns"):
            validate_columns(lf, ["nonexistent"])

    def test_empty_required(self, sample_csv):
        lf = load_file(sample_csv)
        # Should not raise with empty list
        validate_columns(lf, [])
