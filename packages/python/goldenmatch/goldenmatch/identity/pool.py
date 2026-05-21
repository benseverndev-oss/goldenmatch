"""Process-singleton Postgres connection pool for IdentityStore.

Distributed identity resolution opens many short-lived store sessions per
Ray worker. Creating a fresh ``psycopg.connect`` per call costs ~5-15ms +
a TCP handshake and leaks `max_connections` quickly. ``psycopg_pool``'s
``ConnectionPool`` keeps a warm pool of connections per process.

Singleton scope: per Python process, keyed by DSN. Switching DSN closes
the prior pool and opens a new one. Tests call ``reset_identity_pool()``
between fixtures to guarantee a fresh pool.
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_pool: Any = None
_pool_dsn: str | None = None


def get_identity_pool(
    dsn: str, *, min_size: int = 2, max_size: int = 8
) -> Any:
    """Return the process singleton ``ConnectionPool`` for ``dsn``.

    Lazy imports ``psycopg_pool`` so users on plain ``pip install goldenmatch``
    (without the ``[postgres]`` extra) don't pay for the dependency at import.
    """
    global _pool, _pool_dsn
    with _lock:
        if _pool is not None and _pool_dsn == dsn:
            return _pool
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            max_idle=1800.0,
            open=True,
        )
        _pool_dsn = dsn
        return _pool


def reset_identity_pool() -> None:
    """Close the singleton pool (test hook + graceful shutdown)."""
    global _pool, _pool_dsn
    with _lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
        _pool = None
        _pool_dsn = None
