"""``IdentityConfig`` knobs must reach the ``IdentityStore`` constructor.

``identity.schema`` was declared on ``IdentityConfig`` but read by nothing:
``_open_identity_store`` built the store from ``backend``/``path``/``connection``
only, so a user setting ``identity.backend: snowflake`` was pinned to the
constructor defaults with no way to change either the database or the schema --
a documented knob that did nothing. ``identity.database`` did not exist at all.

``_open_identity_store`` swallows construction failures
(``except Exception: logger.warning(...)``), so a broken wiring here surfaces as
a silently absent identity graph rather than an error. That is exactly why these
assert on the kwargs the constructor RECEIVES rather than on any downstream
effect.
"""
from __future__ import annotations

import pytest


def _config(**identity_kwargs):
    from goldenmatch.config.schemas import GoldenMatchConfig

    return GoldenMatchConfig.model_validate(
        {"identity": {"enabled": True, **identity_kwargs}}
    )


def _capture(monkeypatch):
    """Replace IdentityStore with a recorder and return the recording list."""
    import goldenmatch.identity as identity_pkg

    seen: list[dict] = []

    class _Recorder:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(identity_pkg, "IdentityStore", _Recorder)
    return seen


def test_database_and_schema_reach_the_store(monkeypatch) -> None:
    from goldenmatch.core.pipeline import _open_identity_store

    seen = _capture(monkeypatch)
    cfg = _config(
        backend="snowflake", connection="acct",
        database="analytics_db", schema="identity_schema",
    )
    assert _open_identity_store(cfg) is not None

    assert len(seen) == 1, seen
    kwargs = seen[0]
    assert kwargs["backend"] == "snowflake"
    assert kwargs["connection"] == "acct"
    assert kwargs["database"] == "analytics_db"
    assert kwargs["schema"] == "identity_schema"


def test_defaults_reach_the_store(monkeypatch) -> None:
    """Even unset, both values must be PASSED, not left to two sets of defaults."""
    from goldenmatch.core.pipeline import _open_identity_store

    seen = _capture(monkeypatch)
    assert _open_identity_store(_config()) is not None

    kwargs = seen[0]
    assert "database" in kwargs, "identity.database never reaches IdentityStore"
    assert "schema" in kwargs, "identity.schema never reaches IdentityStore"


def test_defaults_match_the_store_constructor() -> None:
    """The config defaults must not silently override the store's own.

    ``_open_identity_store`` now passes these unconditionally, so a divergent
    config default would change behaviour for every user who never set them.
    """
    import inspect

    from goldenmatch.config.schemas import IdentityConfig
    from goldenmatch.identity.store import IdentityStore

    params = inspect.signature(IdentityStore.__init__).parameters
    fields = IdentityConfig.model_fields
    assert fields["database"].default == params["database"].default
    assert fields["schema_"].default == params["schema"].default


@pytest.mark.parametrize("key", ["schema", "database"])
def test_config_accepts_the_public_key(key) -> None:
    """``schema`` is exposed by alias (``schema_`` shadows BaseModel.schema)."""
    from goldenmatch.config.schemas import IdentityConfig

    cfg = IdentityConfig.model_validate({key: "WAREHOUSE_X"})
    value = cfg.schema_ if key == "schema" else cfg.database
    assert value == "WAREHOUSE_X"


def test_disabled_identity_opens_nothing(monkeypatch) -> None:
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import _open_identity_store

    seen = _capture(monkeypatch)
    cfg = GoldenMatchConfig.model_validate({"identity": {"enabled": False}})
    assert _open_identity_store(cfg) is None
    assert seen == []
