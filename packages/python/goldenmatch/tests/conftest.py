import os
import sys
from pathlib import Path

import polars as pl
import pytest

# make scripts/ importable as top-level modules (arrow_finish_line_sweep, etc.)
_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# --- TEMPORARY crash diagnostic (GOLDENMATCH_CRASH_DIAG=1) ------------------
# Gated, inert unless the env var is set. Enables faulthandler (so a SIGSEGV in
# any thread dumps a C traceback -> distinguishes a real crash from an OOM
# SIGKILL, which is uncatchable) and logs process RSS high-water at each test
# start, so the LAST line before a worker dies names the culprit test + the RSS
# just before death. Used to diagnose the `python_goldenmatch (3)` worker crash
# on tests/test_suggest_full_dist.py. Remove once diagnosed.
if os.environ.get("GOLDENMATCH_CRASH_DIAG") == "1":
    import faulthandler as _fh

    # Write the fault handler dump to a per-process FILE (not stderr): when an
    # xdist worker crashes hard, its buffered stderr is lost, so a SIGSEGV/SIGABRT
    # C-traceback never reaches the master log. A file survives the worker death.
    # Also log the currently-running test to the same file so the LAST line names
    # the culprit even without a signal (e.g. a hard _exit()).
    _fh_path = f"/tmp/gm_fh_{os.getpid()}.txt"
    _fh_file = open(_fh_path, "w", buffering=1)  # line-buffered
    _fh.enable(file=_fh_file, all_threads=True)

    def pytest_runtest_logstart(nodeid, location):  # noqa: D401
        try:
            import resource
            hwm = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            _fh_file.write(f"[crashdiag] VmHWM~{hwm:.0f}MB start {nodeid}\n")
            _fh_file.flush()
            # Arm a hang-catcher: if THIS test runs longer than 90s (the crashing
            # test's siblings finish in <=46s, and the worker dies before the 120s
            # pytest-timeout can os._exit it), dump EVERY thread's stack to the
            # surviving file -> the exact native frame where it's parked.
            _fh.dump_traceback_later(90, repeat=False, file=_fh_file, exit=False)
        except Exception:  # never let the diagnostic break a run
            pass

    def pytest_runtest_logfinish(nodeid, location):  # noqa: D401
        try:
            _fh.cancel_dump_traceback_later()  # test finished in time; disarm
        except Exception:
            pass
# --- end crash diagnostic ---------------------------------------------------


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
