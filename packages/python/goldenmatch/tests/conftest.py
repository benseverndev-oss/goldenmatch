from pathlib import Path

import polars as pl
import pytest


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Default-off the cross-run autoconfig memory in every test.

    Cross-test poisoning is otherwise possible: test A runs ``auto_configure_df``
    on a frame with shape S, test B runs it on a different frame with the same
    shape S, and test B silently picks up test A's cached config. Tests that
    specifically want to exercise memory should pass an explicit
    ``AutoConfigMemory`` instance into the controller they construct.

    The env var is read at module import time, so we also patch the cached
    module state directly to make the fixture effective for tests that import
    goldenmatch transitively before this fixture runs.
    """
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    try:
        import goldenmatch.core.autoconfig as _ac
        monkeypatch.setattr(_ac, "_AUTOCONFIG_MEMORY_DISABLED", True, raising=False)
        monkeypatch.setattr(_ac, "_DEFAULT_MEMORY", None, raising=False)
    except ImportError:
        # goldenmatch not importable — skip fixture (e.g. import-failure
        # collection-time tests); env var still set, so any later import
        # picks up the disabled state.
        pass


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_csv(tmp_path) -> Path:
    path = tmp_path / "sample.csv"
    df = pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "first_name": ["John", "john", "Jane", "JOHN", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Smyth", "Jones"],
        "email": ["john@example.com", "john@example.com", "jane@test.com", "john.s@example.com", "bob@test.com"],
        "zip": ["19382", "19382", "10001", "19383", "90210"],
        "phone": ["267-555-1234", "267-555-1234", "212-555-9999", "267-555-1235", "310-555-0000"],
    })
    df.write_csv(path)
    return path


@pytest.fixture
def sample_csv_b(tmp_path) -> Path:
    path = tmp_path / "sample_b.csv"
    df = pl.DataFrame({
        "id": [101, 102, 103],
        "first_name": ["John", "Alice", "Jane"],
        "last_name": ["Smith", "Wonder", "Doe"],
        "email": ["jsmith@work.com", "alice@test.com", "jane@test.com"],
        "zip": ["19382", "30301", "10001"],
        "phone": ["267-555-1234", "404-555-1111", "212-555-9999"],
    })
    df.write_csv(path)
    return path


@pytest.fixture
def sample_parquet(tmp_path) -> Path:
    path = tmp_path / "sample.parquet"
    df = pl.DataFrame({
        "id": [1, 2, 3],
        "first_name": ["John", "Jane", "Bob"],
        "last_name": ["Smith", "Doe", "Jones"],
        "email": ["john@example.com", "jane@test.com", "bob@test.com"],
        "zip": ["19382", "10001", "90210"],
    })
    df.write_parquet(path)
    return path
