"""Postgres test fixture helpers.

Two modes:

- **CI / services container.** When `GOLDENMATCH_TEST_DATABASE_URL` is set
  (typical CI shape: `postgresql://postgres:postgres@localhost:5432/postgres`),
  every fixture invocation provisions a unique database off that admin URL
  (`gm_test_<uuid>`), yields a small URL-holder pointing at it, and drops the
  database on teardown. This keeps tests isolated against a single shared
  Postgres services container without per-test container churn.

- **Local fallback.** When the env var is unset, fall back to
  `testing.postgresql` which spawns its own ephemeral Postgres. Skip the test
  if neither path is available.

The yielded object exposes a `.url()` method so the existing fixtures can pass
it straight to `PostgresConnector(url())` regardless of which mode we're in.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import pytest

_ADMIN_URL_ENV = "GOLDENMATCH_TEST_DATABASE_URL"


def _has_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except Exception:
        return False


def _has_testing_postgresql() -> bool:
    try:
        import testing.postgresql  # noqa: F401
        return True
    except Exception:
        return False


# Module-level capability flag preserved for `skipif` decorators used in tests.
# True when *either* a services-container admin URL is configured OR
# `testing.postgresql` is importable locally.
HAS_POSTGRES = bool(os.environ.get(_ADMIN_URL_ENV)) or (
    _has_testing_postgresql() and _has_psycopg2()
)


class _UrlHolder:
    """Tiny shim that mirrors testing.postgresql.Postgresql().url()."""

    def __init__(self, url: str) -> None:
        self._url = url

    def url(self) -> str:
        return self._url


def _provision_db_from_admin_url(admin_url: str) -> tuple[_UrlHolder, str, str]:
    """Create a fresh database off the admin URL. Returns (holder, db_name, admin_url)."""
    import psycopg2

    parsed = urlparse(admin_url)
    db_name = f"gm_test_{uuid.uuid4().hex[:12]}"

    # Connect to the admin DB (typically 'postgres') to issue CREATE DATABASE.
    admin_conn = psycopg2.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        admin_conn.close()

    # Build the per-test URL by swapping the database path component.
    new_url = urlunparse(parsed._replace(path=f"/{db_name}"))
    return _UrlHolder(new_url), db_name, admin_url


def _drop_db(admin_url: str, db_name: str) -> None:
    import psycopg2

    admin_conn = psycopg2.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            # Terminate any leftover connections (psycopg2 sometimes leaves one
            # in flight when a connector close errored on Windows etc.).
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        admin_conn.close()


def pg_url_fixture() -> Iterator[_UrlHolder]:
    """Generator suitable for use inside a pytest fixture (`yield from`)."""
    admin_url = os.environ.get(_ADMIN_URL_ENV)
    if admin_url:
        if not _has_psycopg2():
            pytest.skip("psycopg2 not installed")
        holder, db_name, admin = _provision_db_from_admin_url(admin_url)
        try:
            yield holder
        finally:
            _drop_db(admin, db_name)
        return

    # Local fallback: testing.postgresql.
    if not (_has_testing_postgresql() and _has_psycopg2()):
        pytest.skip(
            "PostgreSQL not available "
            f"(set {_ADMIN_URL_ENV} or install testing.postgresql + psycopg2)"
        )
    import testing.postgresql

    with testing.postgresql.Postgresql() as postgresql:
        yield postgresql  # already exposes .url()
