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


@pytest.fixture(scope="session", autouse=True)
def _ray_ci_init():
    """Pre-initialize Ray once with CI-safe settings so the production
    ``ray.init(ignore_reinit_error=True, ...)`` calls inside
    ``goldenmatch.distributed`` become no-ops.

    Default ``ray.init()`` hangs on ubuntu-latest CI: the first ray.data op
    blocks forever on the stats-actor RPC because node-IP autodetection +
    dashboard startup never settle on the 2-core runner. Forcing a fresh local
    head on 127.0.0.1 with the dashboard off avoids it. No-op when ray isn't
    installed (every non-distributed lane) or when RAY_ADDRESS points at a real
    cluster (the bench workflows)."""
    import os

    try:
        import ray
    except Exception:
        yield
        return

    if not ray.is_initialized():
        init_kwargs: dict[str, object] = dict(
            num_cpus=2,
            include_dashboard=False,
            configure_logging=False,
            log_to_driver=False,
            ignore_reinit_error=True,
        )
        if not os.environ.get("RAY_ADDRESS"):
            # Force a brand-new local head; skip the slow/hanging IP autodetect.
            init_kwargs["address"] = "local"
            init_kwargs["_node_ip_address"] = "127.0.0.1"
        try:
            ray.init(**init_kwargs)
        except Exception:
            # Don't block the suite on fixture setup — let the production code
            # init ray itself if the constrained init somehow fails.
            pass
    yield
    try:
        ray.shutdown()
    except Exception:
        pass
