"""Every public IdentityStore method must have a Snowflake branch."""
from __future__ import annotations

import inspect

import pytest

fakesnow = pytest.importorskip("fakesnow")

# Methods that legitimately have no Snowflake branch, with the reason.
_EXEMPT = {
    "write_pipeline",        # psycopg pipeline mode; no-ops via its own guard
    "bulk_copy_barrier",     # suspends a psycopg pipeline; nothing to suspend
    "initial_load_writes",   # Postgres from-empty COPY path; no-ops via its guard
}


def test_every_public_method_dispatches_to_snowflake() -> None:
    from goldenmatch.identity.snowflake_backend import SnowflakeIdentityStore
    from goldenmatch.identity.store import IdentityStore

    missing = []
    for name, member in inspect.getmembers(IdentityStore, inspect.isfunction):
        if name.startswith("_") or name in _EXEMPT or name == "close":
            continue
        if not hasattr(SnowflakeIdentityStore, name):
            missing.append(name)
    assert missing == [], (
        f"SnowflakeIdentityStore is missing: {missing}. Add the method and its "
        f"dispatch branch, or add it to _EXEMPT with a reason."
    )


def test_signatures_match() -> None:
    from goldenmatch.identity.snowflake_backend import SnowflakeIdentityStore
    from goldenmatch.identity.store import IdentityStore

    mismatched = []
    for name, member in inspect.getmembers(IdentityStore, inspect.isfunction):
        if name.startswith("_") or name in _EXEMPT or name == "close":
            continue
        sf = getattr(SnowflakeIdentityStore, name, None)
        if sf is None:
            continue
        # Parameters only, deliberately: inspect.signature() also carries the
        # return annotation, and the two classes are written independently, so
        # comparing whole signatures fails on annotation drift that is not a
        # dispatch defect. Parameters are what dispatch correctness depends on.
        want = inspect.signature(member).parameters
        got = inspect.signature(sf).parameters
        if want != got:
            mismatched.append(
                f"{name}: store({list(want)}) != snowflake({list(got)})"
            )
    assert mismatched == [], "\n".join(mismatched)
