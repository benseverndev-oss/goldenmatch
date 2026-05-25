from pathlib import Path

import polars as pl
import pytest


@pytest.fixture(autouse=True)
def _reset_runtime_exclude_columns():
    """Reset the unified-exclusions ContextVar before AND after each test.

    The CLI commands set ``_RUNTIME_EXCLUDE_COLUMNS`` without always
    resetting in every code path (typer.Exit, downstream raises). Inside
    pytest workers, all tests share one ContextVar context, so a leaked
    value pollutes subsequent ``dedupe_df`` / ``match_df`` / auto-config
    calls. Reset is the cheapest guard.
    """
    try:
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        _RUNTIME_EXCLUDE_COLUMNS.set(None)
    except ImportError:
        pass
    yield
    try:
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
        _RUNTIME_EXCLUDE_COLUMNS.set(None)
    except ImportError:
        pass


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


@pytest.fixture(autouse=True)
def _deterministic_controller_budget(monkeypatch):
    """Make auto-config's iteration count deterministic under ``pytest -n auto``.

    The controller's iteration loop (autoconfig_controller.py) bails out early
    when wall-clock ``elapsed > budget.max_seconds``. Under xdist, workers
    compete for CPU, so the same wall budget completes FEWER iterations than
    single-process -> the controller commits a less-refined config -> tests
    asserting a specific refined outcome (e.g.
    test_low_cardinality_not_promoted_to_exact_matchkey,
    test_learned_blocking_not_triggered_below_50k) fail intermittently. This is
    the documented intermittent ``python (goldenmatch)`` CI flake: the suite
    passes single-process but fails under ``-n auto`` on loaded runners.

    Fix: for the default-budget path (``ControllerBudget.for_dataset``, used by
    ``auto_configure_df``), keep the calibrated iteration cap + sample sizes but
    remove the wall-time early-bail (max_seconds -> inf). The loop is then bound
    only by ``max_iterations`` + convergence, both deterministic regardless of
    CPU contention. Tests that exercise the time budget construct an explicit
    ``ControllerBudget(max_seconds=...)`` and bypass ``for_dataset``, so they
    are unaffected.
    """
    try:
        from goldenmatch.core.autoconfig_controller import ControllerBudget
    except ImportError:
        yield
        return

    _orig_for_dataset = ControllerBudget.for_dataset

    def _patched(cls, n_rows):
        budget = _orig_for_dataset(n_rows)
        budget.max_seconds = float("inf")
        return budget

    monkeypatch.setattr(ControllerBudget, "for_dataset", classmethod(_patched))
    yield


@pytest.fixture(autouse=True)
def _ensure_refdata_plugins_registered():
    """Re-register refdata plugins before every test.

    Per the xdist gotcha in CLAUDE.md: workers don't share state, and
    ``test_plugins.py``'s ``reset_registry`` fixture wipes the singleton
    within a worker. The ``import goldenmatch.refdata`` side-effect
    registration only fires once per worker process, so refdata tests
    scheduled after a plugin-test reset would see an empty registry.

    Each ``register_*`` function is idempotent. Skips silently if a
    refdata submodule isn't importable (slim installs).
    """
    try:
        import goldenmatch.refdata  # noqa: F401  triggers registration
        from goldenmatch.refdata.scorer import register_scorers
        register_scorers()
    except ImportError:
        return
    for module_path in (
        "goldenmatch.refdata.business",
        "goldenmatch.refdata.addresses",
        "goldenmatch.refdata.industries",
    ):
        try:
            mod = __import__(module_path, fromlist=["register_transforms"])
            mod.register_transforms()
        except (ImportError, AttributeError):
            continue


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
