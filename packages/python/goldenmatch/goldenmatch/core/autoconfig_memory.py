"""Cross-dataset memory for the auto-config controller.

SQLite-backed store of past committed configs keyed by data-shape signature.
``_initial_config`` consults the memory to short-circuit the v0 heuristic
when a previous run with the same shape converged successfully.

Tier 4 of the zero-config controller spec.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl
    from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS autoconfig_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_signature TEXT NOT NULL,
    committed_config_json TEXT NOT NULL,
    succeeded INTEGER NOT NULL,
    n_iterations INTEGER NOT NULL,
    f1_proxy REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autoconfig_sig ON autoconfig_runs (profile_signature, created_at DESC);
"""


def profile_signature(df: "pl.DataFrame", *, mode: str = "dedupe") -> str:
    """Compute a coarse shape signature for a DataFrame.

    Same n_cols + same dtype distribution → same signature.
    DBLP-ACM (5 Utf8 cols) ≠ Febrl3 (11 Utf8 cols) ≠ NCVR (10 Utf8 cols).

    Args:
        df: Input DataFrame (target frame in match mode).
        mode: "dedupe" or "match" — dedupe vs match runs are distinct keys even
              with the same shape.

    Returns:
        16-character hex string (truncated SHA-256).
    """
    user_cols = [c for c in df.columns if not c.startswith("__")]
    types = tuple(sorted(str(df.schema[c]) for c in user_cols))
    key = (mode, len(user_cols), types)
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]


class AutoConfigMemory:
    """SQLite-backed memory of past auto-config runs keyed by data-shape signature.

    Default path: ``~/.goldenmatch/autoconfig_memory.db``
    Tests: pass ``db_path=":memory:"`` for isolated in-memory databases.

    Thread safety: each ``AutoConfigMemory`` instance holds its own
    ``sqlite3.Connection``; do not share instances across threads.
    """

    def __init__(self, db_path: "str | Path | None" = None) -> None:
        if db_path is None:
            default_dir = Path.home() / ".goldenmatch"
            default_dir.mkdir(parents=True, exist_ok=True)
            db_path = default_dir / "autoconfig_memory.db"

        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ write

    def remember(
        self,
        signature: str,
        config: "GoldenMatchConfig",
        *,
        succeeded: bool,
        n_iterations: int,
        f1_proxy: float | None = None,
    ) -> None:
        """Persist one completed controller run.

        Args:
            signature: Profile signature from ``profile_signature()``.
            config: The committed ``GoldenMatchConfig`` to store.
            succeeded: True when final health != RED.
            n_iterations: Number of controller iterations completed.
            f1_proxy: Optional proxy metric (mass_above_threshold * (1 - mass_in_borderline)).
        """
        config_json = config.model_dump_json()
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO autoconfig_runs
                    (profile_signature, committed_config_json, succeeded,
                     n_iterations, f1_proxy, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (signature, config_json, int(succeeded), n_iterations, f1_proxy, created_at),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            logger.warning("autoconfig_memory: failed to persist run: %s", exc)

    # ------------------------------------------------------------------ read

    def lookup_best(self, signature: str) -> "GoldenMatchConfig | None":
        """Return the most recent succeeded run's config for this signature.

        Returns None if no successful run exists for this signature.
        """
        from goldenmatch.config.schemas import GoldenMatchConfig

        row = self._conn.execute(
            """
            SELECT committed_config_json
            FROM autoconfig_runs
            WHERE profile_signature = ? AND succeeded = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (signature,),
        ).fetchone()
        if row is None:
            return None
        try:
            return GoldenMatchConfig.model_validate_json(row[0])
        except Exception as exc:
            logger.warning(
                "autoconfig_memory: failed to deserialize cached config for %s: %s",
                signature, exc,
            )
            return None

    def all_for(self, signature: str) -> list[dict]:
        """All runs for a signature, sorted by created_at desc. For diagnostics."""
        rows = self._conn.execute(
            """
            SELECT profile_signature, succeeded, n_iterations, f1_proxy, created_at
            FROM autoconfig_runs
            WHERE profile_signature = ?
            ORDER BY created_at DESC
            """,
            (signature,),
        ).fetchall()
        return [
            {
                "profile_signature": r[0],
                "succeeded": bool(r[1]),
                "n_iterations": r[2],
                "f1_proxy": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ admin

    def clear(self) -> None:
        """Remove all rows. Primarily for tests."""
        self._conn.execute("DELETE FROM autoconfig_runs")
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
