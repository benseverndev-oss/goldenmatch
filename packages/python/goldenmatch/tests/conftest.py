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
    """Initialize Ray for the distributed lane and neutralize the ray.data
    global stats actor.

    On this CI runner ray.data's detached ``_StatsActor`` RPC hangs forever:
    the first ray.data op blocks on ``gen_dataset_id_from_stats_actor`` and
    never returns, regardless of runner size / deps / init params (4 attempts,
    2026-05-26). Ray itself is healthy (execution reaches ``from_items``); only
    the stats actor is unreachable. So we monkeypatch the stats-actor entry
    points to local no-ops. Best-effort + version-tolerant: patches whichever
    names exist and swallows any failure. No-op when ray isn't installed (every
    non-distributed lane)."""
    try:
        import ray
    except Exception:
        yield
        return

    # --- neutralize ray.data's hanging stats actor -------------------------
    # gen_dataset_id_from_stats_actor is the synchronous main-thread blocker;
    # the metric-push methods are defensive (they normally batch on a daemon
    # thread, but register synchronously on some versions). Returning a local
    # id / None is safe: these are fire-and-forget telemetry, not load-bearing.
    try:
        import itertools
        import uuid

        from ray.data._internal import stats as _ds_stats

        _ids = itertools.count(1)

        def _local_dataset_id(*_a, **_k):
            return f"ci_{next(_ids)}_{uuid.uuid4().hex[:8]}"

        def _noop(*_a, **_k):
            return None

        _noop_names = (
            "register_dataset_to_stats_actor",
            "update_execution_metrics",
            "clear_execution_metrics",
            "update_iteration_metrics",
            "clear_iteration_metrics",
        )
        for _t in (getattr(_ds_stats, n, None) for n in ("_StatsManager", "StatsManager")):
            if _t is None:
                continue
            if hasattr(_t, "gen_dataset_id_from_stats_actor"):
                setattr(_t, "gen_dataset_id_from_stats_actor", _local_dataset_id)
            for _m in _noop_names:
                if hasattr(_t, _m):
                    setattr(_t, _m, _noop)
        print("[ray_ci_init] ray.data stats actor neutralized", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ray_ci_init] stats-actor patch skipped: {e!r}", flush=True)

    if not ray.is_initialized():
        try:
            ray.init(
                include_dashboard=False,
                configure_logging=False,
                log_to_driver=False,
                ignore_reinit_error=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ray_ci_init] ray.init failed: {e!r}", flush=True)

    yield
    try:
        ray.shutdown()
    except Exception:
        pass
