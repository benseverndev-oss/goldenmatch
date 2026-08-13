"""#2521: `build_resolved_crosswalk` must respect the caller's identity config.

It used to replace `cfg.identity` wholesale with a freshly built `IdentityConfig`
hardcoding `backend="sqlite"` and omitting `emit_singletons` -- the only two
single-node scale levers the identity docs describe. A caller who followed that
documentation got SQLite with singletons on, silently, with no diagnostic.

These tests assert on the config the resolver is actually handed, by capturing it
at the `dedupe_df` boundary, so they fail if the merge regresses regardless of
what the store does afterwards.
"""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import IdentityConfig


def _frame():
    return pa.table({
        "pk": ["1", "2", "3", "4"],
        "name": ["Alice Smith", "Alice Smyth", "Bob Jones", "Robert Jones"],
        "city": ["Leeds", "Leeds", "York", "York"],
    })


def _capture_config(monkeypatch):
    """Intercept the config handed to the resolver, and stop before any store IO."""
    seen: dict = {}

    def _fake_dedupe_df(df, *, config=None, **kw):
        seen["config"] = config
        raise _Stop

    class _Stop(Exception):
        pass

    import goldenmatch._api as api
    monkeypatch.setattr(api, "dedupe_df", _fake_dedupe_df)
    return seen, _Stop


def _run(monkeypatch, **kwargs):
    seen, stop = _capture_config(monkeypatch)
    from goldenmatch.semantic.crosswalk import build_resolved_crosswalk
    with pytest.raises(stop):
        build_resolved_crosswalk(_frame(), source_pk="pk", source_name="s", **kwargs)
    return seen["config"].identity


class TestCallerIdentityIsRespected:
    def test_postgres_backend_and_connection_survive(self, tmp_path):
        # The exact reproduction from the issue: a postgres DSN plus
        # emit_singletons=False, both previously discarded.
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        try:
            cfg = _cfg_with(IdentityConfig(
                enabled=True, backend="postgres",
                connection="postgresql://example/db", emit_singletons=False,
            ))
            identity = _run(monkeypatch, config=cfg)
        finally:
            monkeypatch.undo()
        assert identity.backend == "postgres"
        assert identity.connection == "postgresql://example/db"
        assert identity.emit_singletons is False

    def test_emit_singletons_false_survives_on_sqlite(self, tmp_path):
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        try:
            cfg = _cfg_with(IdentityConfig(enabled=True, emit_singletons=False))
            identity = _run(
                monkeypatch, config=cfg, store_path=str(tmp_path / "x.db"),
            )
        finally:
            monkeypatch.undo()
        assert identity.emit_singletons is False
        assert identity.backend == "sqlite"

    def test_store_path_still_wins_for_the_sqlite_path(self, tmp_path):
        # `store_path` is an explicit argument, so it beats a config default.
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        p = str(tmp_path / "durable.db")
        try:
            cfg = _cfg_with(IdentityConfig(enabled=True, path="/should/not/be/used.db"))
            identity = _run(monkeypatch, config=cfg, store_path=p)
        finally:
            monkeypatch.undo()
        assert identity.path == p

    def test_function_owned_fields_are_overridden(self, tmp_path):
        # These describe THIS call, so the caller does not get to set them.
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        try:
            cfg = _cfg_with(IdentityConfig(
                enabled=False, source_pk_column="wrong", dataset="wrong",
            ))
            identity = _run(
                monkeypatch, config=cfg, store_path=str(tmp_path / "x.db"),
            )
        finally:
            monkeypatch.undo()
        assert identity.enabled is True
        assert identity.source_pk_column == "pk"
        assert identity.dataset == "s"  # defaults to source_name

    def test_untouched_identity_reproduces_the_old_behaviour(self, tmp_path):
        # Backward compatibility is the point: IdentityConfig's defaults ARE what
        # this function used to hardcode, so an untouched config is unchanged.
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        p = str(tmp_path / "x.db")
        try:
            identity = _run(monkeypatch, store_path=p)
        finally:
            monkeypatch.undo()
        assert identity.backend == "sqlite"
        assert identity.emit_singletons is True
        assert identity.path == p
        assert identity.enabled is True


class TestNonSqliteStorePathIsNotSilent:
    def test_store_path_with_postgres_warns(self, tmp_path, caplog):
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        try:
            cfg = _cfg_with(IdentityConfig(
                enabled=True, backend="postgres", connection="postgresql://x/y",
            ))
            with caplog.at_level("WARNING", logger="goldenmatch.semantic.crosswalk"):
                _run(monkeypatch, config=cfg, store_path=str(tmp_path / "ignored.db"))
        finally:
            monkeypatch.undo()
        assert any("is ignored" in r.getMessage() for r in _ours(caplog)), caplog.text

    def test_no_warning_on_the_sqlite_path(self, tmp_path, caplog):
        pytest.importorskip("polars")
        monkeypatch = pytest.MonkeyPatch()
        try:
            with caplog.at_level("WARNING", logger="goldenmatch.semantic.crosswalk"):
                _run(monkeypatch, store_path=str(tmp_path / "x.db"))
        finally:
            monkeypatch.undo()
        # Scope to THIS module's logger: auto-config legitimately warns about the
        # fixture's own shape, and caplog collects every logger's records.
        assert not _ours(caplog), caplog.text


def _ours(caplog):
    return [r for r in caplog.records if r.name == "goldenmatch.semantic.crosswalk"]


def _cfg_with(identity: IdentityConfig):
    """A minimal real config carrying the given identity section."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0)],
        )],
        # A weighted matchkey requires blocking (schema-enforced).
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
        ),
        identity=identity,
    )
