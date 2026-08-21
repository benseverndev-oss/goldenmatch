import os
import sys
from pathlib import Path

import pytest

# make scripts/ importable as top-level modules (arrow_finish_line_sweep, etc.)
_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# Routing env vars that flip which scoring path the pipeline takes. A test that
# mutates one of these via raw ``os.environ[...] = ...`` (rather than
# monkeypatch) and doesn't restore it in a bulletproof ``finally`` leaks the
# value to every later test in the same xdist worker. Since these decide
# ``_use_bucket_scorer`` / the columnar lane, a leak silently flips pure-function
# routing assertions (``test_learned_lowering_parity``) and the frames-out
# lazy-cluster wiring (``test_lazy_cluster_dict``) — failures that surface only
# in the full suite, never in isolation.
_ROUTING_ENV_VARS = (
    "GOLDENMATCH_BUCKET_DEFAULT",
    "GOLDENMATCH_COLUMNAR_PIPELINE",
    "GOLDENMATCH_FRAME",
    # test_autoconfig_arrow_native_parity._arrow_native(False) sets this to "0"
    # via raw os.environ with no restore in the helper. It gates the
    # auto_configure_df arrow-native boundary (autoconfig.py: _arrow_native_ac);
    # a leaked "0" forces the polars-import branch, which the no-polars tripwire
    # subprocess in test_match_arrow_parity inherits via os.environ and fails on
    # ("polars blocked (match arrow tripwire)"). Snapshot/restore closes it.
    "GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE",
)


@pytest.fixture(autouse=True)
def _restore_routing_env_vars():
    """Snapshot + restore the scoring-route env vars around EVERY test.

    Airtight against cross-test leakage: whatever a test does to these vars
    (raw ``os.environ`` set/pop, with or without its own cleanup), this fixture
    restores the pre-test value afterward, so pollution can never accumulate
    across tests in a worker. Same class as ``_reset_runtime_exclude_columns``
    / ``_reset_profile_emitter_stack`` below; process-env is the shared state
    here instead of a ContextVar.
    """
    snapshot = {k: os.environ.get(k) for k in _ROUTING_ENV_VARS}
    yield
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


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
def _reset_profile_emitter_stack():
    """Drain the profile-emitter stack before AND after each test.

    ``core.profile_emitter._emitter_stack`` is a ContextVar shared by every
    test in an xdist worker. A test that leaves an emitter active (a manual
    ``current_emitter()`` push, or a ``profile_capture()`` that unwound through
    an unusual path) makes ``has_active_emitter()`` return True for every
    subsequent test in that worker. That silently flips ``_use_bucket_scorer``
    onto the legacy per-block path (it deliberately declines while profiling),
    so pure-function routing assertions (``test_learned_lowering_parity``) and
    the frames-out lazy-cluster wiring (``test_lazy_cluster_dict``) fail only
    in the full suite, never in isolation. Same class as
    ``_reset_runtime_exclude_columns`` above; an empty stack is the clean state.
    """
    try:
        import goldenmatch.core.profile_emitter as _pe
        _pe._emitter_stack.set(())
    except ImportError:
        pass  # goldenmatch not importable (import-failure collection tests) -> no stack to reset
    yield
    try:
        import goldenmatch.core.profile_emitter as _pe
        _pe._emitter_stack.set(())
    except ImportError:
        pass  # goldenmatch not importable (import-failure collection tests) -> no stack to reset


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


# The autouse `_ensure_refdata_plugins_registered` fixture that used to live
# here is gone: `PluginRegistry.reset()` now replays the bundled registrations
# onto the fresh singleton (`PluginRegistry.add_bootstrap`, wired in
# `goldenmatch/refdata/__init__.py`), so the library no longer loses its own
# plugins to a reset and the test harness has nothing to paper over.
#
# Keeping it would have been worse than redundant. It re-registered a
# HAND-MAINTAINED list -- business, addresses, industries, scorer -- that had
# drifted: `business_aliases` and `core.acronym` were never added. So
# `refdata_business_canonical` alone stayed unregistered after any resetting
# test, and `test_business_aliases.py::test_alias_transforms_registered` failed
# if and only if xdist put a resetting test ahead of it in the same worker.
# That is the "shard-isolation-fragile" flake class the CI --deselect list
# documents; a fix-list that must be kept in sync by hand is the mechanism.


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


# Sample-data fixtures are built with pyarrow (the hard dep) so test collection
# stays polars-free — goldenmatch is arrow-native and polars is an OPTIONAL extra
# (root pyproject D6). Importing polars at module scope broke collection whenever
# the [polars] extra wasn't installed.
@pytest.fixture
def sample_csv(tmp_path) -> Path:
    import pyarrow as pa
    import pyarrow.csv as pacsv

    path = tmp_path / "sample.csv"
    table = pa.table({
        "id": [1, 2, 3, 4, 5],
        "first_name": ["John", "john", "Jane", "JOHN", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Smyth", "Jones"],
        "email": ["john@example.com", "john@example.com", "jane@test.com", "john.s@example.com", "bob@test.com"],
        "zip": ["19382", "19382", "10001", "19383", "90210"],
        "phone": ["267-555-1234", "267-555-1234", "212-555-9999", "267-555-1235", "310-555-0000"],
    })
    pacsv.write_csv(table, path)
    return path


@pytest.fixture
def sample_csv_b(tmp_path) -> Path:
    import pyarrow as pa
    import pyarrow.csv as pacsv

    path = tmp_path / "sample_b.csv"
    table = pa.table({
        "id": [101, 102, 103],
        "first_name": ["John", "Alice", "Jane"],
        "last_name": ["Smith", "Wonder", "Doe"],
        "email": ["jsmith@work.com", "alice@test.com", "jane@test.com"],
        "zip": ["19382", "30301", "10001"],
        "phone": ["267-555-1234", "404-555-1111", "212-555-9999"],
    })
    pacsv.write_csv(table, path)
    return path


@pytest.fixture
def sample_parquet(tmp_path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "sample.parquet"
    table = pa.table({
        "id": [1, 2, 3],
        "first_name": ["John", "Jane", "Bob"],
        "last_name": ["Smith", "Doe", "Jones"],
        "email": ["john@example.com", "jane@test.com", "bob@test.com"],
        "zip": ["19382", "10001", "90210"],
    })
    pq.write_table(table, path)
    return path


@pytest.fixture(scope="module")
def spark():
    """A Spark Connect session, from whichever server the env selects.

    Backend-agnostic ON PURPOSE (P0, spec
    ``2026-08-10-spark-native-execution-design``). ``GOLDENMATCH_SPARK_REMOTE``:

      unset             -> spawn a local pysail ``SparkConnectServer`` (prior behaviour)
      ``"local[*]"``    -> Apache Spark's own local Connect server (pyspark >= 4)
      ``"sc://host:p"`` -> an already-running Connect endpoint

    Nine test files each carried a copy of this hardcoding
    ``pysail.spark.SparkConnectServer``. That is *why* the tier had never been
    run against real Spark: the tests could not express it. Every import is
    inside the body, so a suite that never requests this fixture pays nothing.
    """
    import os

    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE")

    if not remote:
        pytest.importorskip("pysail")
        from pysail.spark import SparkConnectServer

        server = SparkConnectServer()
        server.start()
        _, port = server.listening_address
        sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
        yield sess
        sess.stop()
        server.stop()
        return

    # Real Spark owns its own server lifecycle; nothing to stop but the session.
    sess = SparkSession.builder.remote(remote).getOrCreate()

    # P1: real Spark FORKS a Python worker with its own environment, so the
    # client's site-packages are not on it. Ship a packed venv when one is
    # provided. Unset -> unchanged (the pysail path never needs this: its worker
    # shares the client interpreter, which is exactly why P0's failure class was
    # invisible until a real backend ran).
    archive = os.environ.get("GOLDENMATCH_SPARK_PYENV")
    if archive:
        from goldenmatch.spark.deps import ship_python_environment

        ship_python_environment(sess, archive)

    yield sess
    sess.stop()
