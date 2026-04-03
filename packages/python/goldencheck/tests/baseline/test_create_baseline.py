"""Tests for goldencheck.baseline.create_baseline and load_baseline — TDD."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from goldencheck.baseline import create_baseline, load_baseline
from goldencheck.baseline.models import BaselineProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(n: int = 100) -> pl.DataFrame:
    """Return a simple DataFrame with mixed column types for baseline testing."""
    import numpy as np

    rng = np.random.default_rng(42)
    return pl.DataFrame({
        "age": rng.integers(18, 80, size=n).tolist(),
        "salary": (rng.normal(50_000, 15_000, size=n)).tolist(),
        "email": [f"user{i}@example.com" for i in range(n)],
        "status": ["active" if i % 3 != 0 else "inactive" for i in range(n)],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateBaselineFromDataFrame:
    """create_baseline() called with a Polars DataFrame."""

    def test_returns_baseline_profile_instance(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["priors"])
        assert isinstance(profile, BaselineProfile)

    def test_rows_and_columns_populated(self) -> None:
        df = _make_df(n=80)
        profile = create_baseline(df, skip=["priors"])
        assert profile.rows == 80
        assert set(profile.columns) == {"age", "salary", "email", "status"}

    def test_has_statistical_profiles(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["priors"])
        # At least the two numeric columns should be profiled.
        assert "age" in profile.stat_profiles
        assert "salary" in profile.stat_profiles

    def test_has_semantic_types(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["priors"])
        # email column should be mapped to the "email" semantic type.
        assert "email" in profile.semantic_types
        assert profile.semantic_types["email"] == "email"

    def test_source_is_empty_string_by_default(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["priors"])
        assert profile.source == ""

    def test_custom_source_label(self) -> None:
        df = _make_df()
        profile = create_baseline(df, source="my_dataset", skip=["priors"])
        assert profile.source == "my_dataset"

    def test_priors_skipped_for_dataframe_input(self) -> None:
        """Priors require a file path; they should be empty for DataFrame input."""
        df = _make_df()
        profile = create_baseline(df)  # no skip=["priors"]
        assert profile.priors == {}


class TestCreateBaselineFromPath:
    """create_baseline() called with a file path."""

    def test_reads_csv_and_sets_source(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        df = _make_df(n=60)
        df.write_csv(str(csv_file))

        profile = create_baseline(csv_file, skip=["priors"])
        assert profile.rows == 60
        assert str(csv_file) == profile.source

    def test_source_filename_property(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "mydata.csv"
        _make_df(n=50).write_csv(str(csv_file))
        profile = create_baseline(csv_file, skip=["priors"])
        assert profile.source_filename == "mydata.csv"

    def test_string_path_accepted(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        _make_df(n=50).write_csv(str(csv_file))
        profile = create_baseline(str(csv_file), skip=["priors"])
        assert isinstance(profile, BaselineProfile)


class TestSkipTechniques:
    """skip= parameter omits the requested techniques."""

    def test_skip_correlation_leaves_correlations_empty(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["correlation", "priors"])
        assert profile.correlations == []

    def test_skip_semantic_leaves_semantic_types_empty(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["semantic", "priors"])
        assert profile.semantic_types == {}

    def test_skip_statistical_leaves_stat_profiles_empty(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["statistical", "priors"])
        assert profile.stat_profiles == {}

    def test_skip_patterns_leaves_patterns_empty(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["patterns", "priors"])
        assert profile.patterns == {}

    def test_skip_constraints_leaves_constraints_empty(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["constraints", "priors"])
        assert profile.constraints_fd == []
        assert profile.constraints_keys == []
        assert profile.constraints_temporal == []

    def test_skip_multiple_techniques(self) -> None:
        df = _make_df()
        profile = create_baseline(df, skip=["correlation", "semantic", "priors"])
        assert profile.correlations == []
        assert profile.semantic_types == {}


class TestSaveLoadRoundTrip:
    """BaselineProfile can be saved to YAML and reloaded via load_baseline()."""

    def test_round_trip_preserves_rows_and_columns(self, tmp_path: Path) -> None:
        df = _make_df(n=60)
        profile = create_baseline(df, skip=["priors"])
        yaml_path = tmp_path / "baseline.yml"
        profile.save(str(yaml_path))

        loaded = load_baseline(yaml_path)
        assert loaded.rows == profile.rows
        assert loaded.columns == profile.columns

    def test_round_trip_preserves_semantic_types(self, tmp_path: Path) -> None:
        df = _make_df(n=60)
        profile = create_baseline(df, skip=["priors"])
        yaml_path = tmp_path / "baseline.yml"
        profile.save(str(yaml_path))

        loaded = load_baseline(yaml_path)
        assert loaded.semantic_types == profile.semantic_types

    def test_round_trip_preserves_stat_profiles(self, tmp_path: Path) -> None:
        df = _make_df(n=60)
        profile = create_baseline(df, skip=["priors"])
        yaml_path = tmp_path / "baseline.yml"
        profile.save(str(yaml_path))

        loaded = load_baseline(yaml_path)
        assert set(loaded.stat_profiles.keys()) == set(profile.stat_profiles.keys())

    def test_load_baseline_path_as_string(self, tmp_path: Path) -> None:
        df = _make_df(n=50)
        profile = create_baseline(df, skip=["priors"])
        yaml_path = tmp_path / "baseline.yml"
        profile.save(str(yaml_path))

        loaded = load_baseline(str(yaml_path))
        assert isinstance(loaded, BaselineProfile)
        assert loaded.source == profile.source


class TestSampling:
    """Large DataFrames are downsampled before profiling."""

    def test_downsampled_rows_stored_in_profile(self) -> None:
        import numpy as np

        rng = np.random.default_rng(0)
        big_df = pl.DataFrame({"x": rng.normal(size=2_000).tolist()})
        profile = create_baseline(big_df, sample_size=100, skip=["priors"])
        # The stored row count must equal the sample size, not the original.
        assert profile.rows == 100
