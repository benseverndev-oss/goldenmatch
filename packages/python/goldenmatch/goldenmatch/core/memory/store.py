"""MemoryStore -- SQLite/Postgres persistence for corrections and adjustments."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

log = logging.getLogger("goldenmatch.memory")


class CorrectionSource(StrEnum):
    """Canonical correction source identifiers.

    StrEnum members ARE strings, so existing call sites that pass raw
    "steward"/"boost"/etc. continue to work. These enums are reference
    values for callers/tests/lookups, not field type changes.
    """
    STEWARD = "steward"
    BOOST = "boost"
    UNMERGE = "unmerge"
    AGENT = "agent"
    LLM = "llm"
    API = "api"


class Decision(StrEnum):
    """Canonical correction decisions."""
    APPROVE = "approve"
    REJECT = "reject"
    # #437: field-level golden-record correction (inline-edit of a
    # chosen field value). `field_name` + `original_value` +
    # `corrected_value` carry the edit on the Correction row.
    FIELD_CORRECT = "field_correct"
    # v1.20.x: cluster-level approve/reject decision (RFC from MJH
    # Print Modernization, 2026-05-22). `cluster_score` +
    # `cluster_outcome` carry the decision payload. Consumed by
    # `goldenmatch.core.autoconfig_cluster_threshold_tuner`.
    CLUSTER_DECISION = "cluster_decision"


HIGH_TRUST_SOURCES: frozenset[CorrectionSource] = frozenset({
    CorrectionSource.STEWARD,
    CorrectionSource.BOOST,
    CorrectionSource.UNMERGE,
})


def trust_for_source(source: str | CorrectionSource) -> float:
    """Return 1.0 for human-trust sources (steward/boost/unmerge), 0.5 else.

    Accepts a raw string OR a CorrectionSource member. Centralizes the trust
    mapping so call sites cannot drift.
    """
    return 1.0 if source in HIGH_TRUST_SOURCES else 0.5


@dataclass
class Correction:
    """A single correction stored in memory.

    Two shapes supported:

    1. Pair-level (the original shape): operator decides a candidate
       merge pair is approve / reject / split. `id_a`, `id_b`,
       `decision` carry the verdict. `field_hash` is an opaque privacy
       hash that lets us re-anchor without storing PII. This is what
       MemoryLearner consumes for threshold tuning.

    2. Field-level (added 2026-05-22 for #437): operator inline-edits
       a chosen golden-record field. `field_name` + `original_value` +
       `corrected_value` carry the edit. `id_a` is the cluster_id;
       `id_b` is unused (set to 0). `decision` is "field_correct".

    `tune_field_strategy` consumes the field-level shape -- given a
    correction with `field_name="address1"` and `corrected_value="X"`,
    it can ask "would `most_recent` have predicted X given the cluster's
    member values?" and tally hit rates per strategy.

    Older pair-level corrections (field_name=None) keep working
    unchanged. The tuner's `_strategy_would_match` falls back to its
    coarse decision/trust heuristic for those.
    """
    id: str
    id_a: int
    id_b: int
    decision: str                # Decision value (StrEnum members serialize as str)
    source: str                  # CorrectionSource value
    trust: float                 # 1.0 (HIGH_TRUST_SOURCES) or 0.5 (else)
    field_hash: str
    record_hash: str
    original_score: float
    matchkey_name: str | None = None
    reason: str | None = None
    dataset: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    # Field-level golden corrections (#437, 2026-05-22). All three
    # default to None to preserve backward compat with pair-level
    # corrections. When set, the tuner uses them to learn per-field
    # strategy preferences from real inline-edit feedback.
    field_name: str | None = None
    original_value: str | None = None
    corrected_value: str | None = None
    # Cluster-level decisions (v1.20.x, MJH Print Modernization RFC).
    # Set when `decision == "cluster_decision"`. The tuner consumes
    # these via `tune_decision_threshold()`. `id_a` carries
    # `cluster_id`; `id_b` is unused (set to 0).
    cluster_score: float | None = None
    cluster_outcome: str | None = None  # "approve" | "reject"


@dataclass
class LearnedAdjustment:
    """Output of the rule learner."""
    matchkey_name: str
    threshold: float | None = None
    field_weights: dict[str, float] | None = None
    sample_size: int = 0
    learned_at: datetime = field(default_factory=datetime.now)


def _canon_pair(id_a: int, id_b: int) -> tuple[int, int]:
    """Canonicalize pair ordering to (min, max)."""
    return (min(id_a, id_b), max(id_a, id_b))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    id_a INTEGER, id_b INTEGER,
    decision TEXT, source TEXT, trust REAL,
    field_hash TEXT, record_hash TEXT,
    original_score REAL,
    matchkey_name TEXT,
    reason TEXT, dataset TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- #437: field-level golden-record corrections. NULL for pair-level
    -- (decision in {approve, reject}). Set when decision='field_correct'.
    field_name TEXT,
    original_value TEXT,
    corrected_value TEXT,
    -- v1.20.x: cluster-level decisions (MJH RFC). Set when
    -- decision='cluster_decision'. `id_a` carries cluster_id.
    cluster_score REAL,
    cluster_outcome TEXT,
    UNIQUE(id_a, id_b, dataset)
);
CREATE INDEX IF NOT EXISTS idx_corrections_pair ON corrections(id_a, id_b, dataset);

CREATE TABLE IF NOT EXISTS adjustments (
    matchkey_name TEXT PRIMARY KEY,
    threshold REAL, field_weights TEXT,
    sample_size INTEGER,
    learned_at TIMESTAMP
);
"""


class MemoryStore:
    """Persistence layer for Learning Memory."""

    def __init__(
        self,
        backend: str = "sqlite",
        path: str = ".goldenmatch/memory.db",
        connection: str | None = None,
    ) -> None:
        self._backend = backend
        if backend == "sqlite":
            import os
            import sqlite3  # noqa: PLC0415 -- lazy, see #364
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(path)
            self._conn.row_factory = sqlite3.Row
            # #130: enable WAL mode for concurrent-write reliability.
            # Default journal_mode=delete intermittently raises "database
            # is locked" when two MemoryStore instances write to the same
            # file. WAL allows one writer + multiple readers concurrently
            # and is the right default for shared SQLite memory stores.
            # `PRAGMA journal_mode=WAL` is per-database, persisted in the
            # file header, and a no-op on subsequent opens.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate_field_correction_columns()
            self._migrate_cluster_decision_columns()
            # %r quotes the path so a newline/control char in a caller-supplied
            # db path can't forge log lines (CodeQL #301 log-injection).
            log.debug("MemoryStore opened: %r (journal_mode=WAL)", path)
        else:
            raise NotImplementedError(f"Backend '{backend}' not yet implemented")

    def _migrate_field_correction_columns(self) -> None:
        """#437: add field_name/original_value/corrected_value to
        pre-existing DBs that were created before v1.18.2.

        Idempotent: SQLite raises OperationalError when a column
        already exists; we swallow it. CREATE TABLE IF NOT EXISTS at
        open time covers fresh DBs. The field_name index is created
        AFTER the columns exist (so this method must run before any
        DDL that references field_name).
        """
        for col in ("field_name", "original_value", "corrected_value"):
            try:
                self._conn.execute(f"ALTER TABLE corrections ADD COLUMN {col} TEXT")
            except Exception as exc:  # pragma: no cover -- benign idempotency
                msg = str(exc).lower()
                if "duplicate" not in msg:
                    log.debug("field-correction migration skipped %s: %s", col, exc)
        # Now that the columns are guaranteed to exist, create the
        # field-name lookup index. Idempotent via IF NOT EXISTS.
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_field "
                "ON corrections(dataset, field_name) WHERE field_name IS NOT NULL"
            )
        except Exception as exc:  # pragma: no cover -- benign
            log.debug("field-correction index skipped: %s", exc)

    def _migrate_cluster_decision_columns(self) -> None:
        """v1.20.x: add cluster_score / cluster_outcome columns for
        pre-existing DBs.

        Idempotent: SQLite raises OperationalError on duplicate column;
        swallow it. Pattern mirrors `_migrate_field_correction_columns`.
        Spec: docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md
        """
        for col, sql_type in (
            ("cluster_score", "REAL"),
            ("cluster_outcome", "TEXT"),
        ):
            try:
                self._conn.execute(
                    f"ALTER TABLE corrections ADD COLUMN {col} {sql_type}",
                )
            except Exception as exc:  # pragma: no cover -- benign
                msg = str(exc).lower()
                if "duplicate" not in msg:
                    log.debug(
                        "cluster-decision migration skipped %s: %s", col, exc,
                    )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def add_correction(self, correction: Correction) -> None:
        """Upsert a correction. Higher trust wins; same trust = latest wins.

        Pair-level corrections (decision in {approve, reject}) get
        canonicalized to (min, max) ordering before storage.

        Field-level and cluster-decision corrections (decision in
        {field_correct, cluster_decision}) skip canonicalization
        because `id_a` carries `cluster_id` (semantic) and `id_b=0`
        is a sentinel (canonicalizing would swap them and lose the
        cluster_id).
        """
        if correction.decision in ("field_correct", "cluster_decision"):
            ca, cb = correction.id_a, correction.id_b
        else:
            ca, cb = _canon_pair(correction.id_a, correction.id_b)
        existing = self.get_pair_correction(ca, cb, correction.dataset)

        if existing is not None:
            if correction.trust < existing.trust:
                log.debug("Correction ignored (lower trust): (%d, %d)", ca, cb)
                return

        # Atomic upsert: DELETE + INSERT in one transaction
        with self._conn:
            self._conn.execute(
                "DELETE FROM corrections WHERE id_a = ? AND id_b = ? AND dataset IS ?",
                (ca, cb, correction.dataset),
            )
            self._conn.execute(
                "INSERT INTO corrections "
                "(id, id_a, id_b, decision, source, trust, field_hash, record_hash, "
                "original_score, matchkey_name, reason, dataset, created_at, "
                "field_name, original_value, corrected_value, "
                "cluster_score, cluster_outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    correction.id, ca, cb,
                    correction.decision, correction.source, correction.trust,
                    correction.field_hash, correction.record_hash,
                    correction.original_score, correction.matchkey_name,
                    correction.reason, correction.dataset,
                    correction.created_at.isoformat(),
                    correction.field_name, correction.original_value,
                    correction.corrected_value,
                    correction.cluster_score, correction.cluster_outcome,
                ),
            )
        log.debug("Correction stored: (%d, %d) %s [%s]", ca, cb,
                   correction.decision, correction.source)

    def record_cluster_decision(
        self,
        dataset: str,
        cluster_id: int,
        score: float,
        outcome: str,
        source: str = "steward",
        reason: str | None = None,
    ) -> Correction:
        """v1.20.x: convenience wrapper for cluster-decision corrections.

        Constructs a ``decision="cluster_decision"`` Correction, sets
        ``id_a = cluster_id`` and ``id_b = 0`` (unused), and routes
        through ``add_correction``'s trust-aware upsert.

        Args:
            dataset: dataset namespace (e.g. ``"pub_48"``).
            cluster_id: stable cluster id from the dedupe run.
            score: cluster-level scalar in ``[0, 1]``. Semantics are
                consumer-defined (bottleneck pair score, avg edge,
                connectivity, etc.).
            outcome: ``"approve"`` or ``"reject"``.
            source: CorrectionSource value; default ``"steward"``
                (trust=1.0). Use ``"agent"`` (0.5) for non-human signals.
            reason: optional human-readable note.

        Returns:
            The stored Correction (with server-generated ``id``).

        Spec: docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md
        """
        import uuid

        if outcome not in ("approve", "reject"):
            raise ValueError(
                f"outcome must be 'approve' or 'reject'; got {outcome!r}",
            )
        if not (0.0 <= float(score) <= 1.0):
            raise ValueError(
                f"score must be in [0, 1]; got {score!r}",
            )
        trust = trust_for_source(source)
        correction = Correction(
            id=str(uuid.uuid4()),
            id_a=int(cluster_id),
            id_b=0,
            decision="cluster_decision",
            source=source,
            trust=trust,
            field_hash="",
            record_hash="",
            original_score=0.0,
            matchkey_name=None,
            reason=reason,
            dataset=dataset,
            created_at=datetime.now(),
            cluster_score=float(score),
            cluster_outcome=outcome,
        )
        self.add_correction(correction)
        return correction

    def get_pair_correction(
        self, id_a: int, id_b: int, dataset: str | None = None,
    ) -> Correction | None:
        ca, cb = _canon_pair(id_a, id_b)
        if dataset is not None:
            row = self._conn.execute(
                "SELECT * FROM corrections WHERE id_a = ? AND id_b = ? AND dataset = ?",
                (ca, cb, dataset),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM corrections WHERE id_a = ? AND id_b = ? AND dataset IS NULL",
                (ca, cb),
            ).fetchone()
        return self._row_to_correction(row) if row else None

    def get_pair_corrections_bulk(
        self, pairs: list[tuple[int, int]], dataset: str | None = None,
    ) -> dict[tuple[int, int], Correction]:
        all_corrections = self.get_corrections(dataset=dataset)
        lookup = {(c.id_a, c.id_b): c for c in all_corrections}
        # Canonicalize lookup keys from input pairs
        result = {}
        for a, b in pairs:
            ca, cb = _canon_pair(a, b)
            if (ca, cb) in lookup:
                result[(a, b)] = lookup[(ca, cb)]
        return result

    def get_corrections(self, dataset: str | None = None) -> list[Correction]:
        if dataset is not None:
            rows = self._conn.execute(
                "SELECT * FROM corrections WHERE dataset = ? ORDER BY created_at",
                (dataset,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM corrections ORDER BY created_at",
            ).fetchall()
        return [self._row_to_correction(r) for r in rows]

    def count_corrections(self, dataset: str | None = None) -> int:
        if dataset is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM corrections WHERE dataset = ?", (dataset,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM corrections").fetchone()
        return row[0] if row else 0

    def corrections_since(self, since: datetime) -> list[Correction]:
        rows = self._conn.execute(
            "SELECT * FROM corrections WHERE created_at > ? ORDER BY created_at",
            (since.isoformat(),),
        ).fetchall()
        return [self._row_to_correction(r) for r in rows]

    def save_adjustment(self, adj: LearnedAdjustment) -> None:
        weights_json = json.dumps(adj.field_weights) if adj.field_weights else None
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO adjustments "
                "(matchkey_name, threshold, field_weights, sample_size, learned_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (adj.matchkey_name, adj.threshold, weights_json,
                 adj.sample_size, adj.learned_at.isoformat()),
            )
        log.debug("Adjustment saved: %s threshold=%.3f samples=%d",
                   adj.matchkey_name, adj.threshold or 0, adj.sample_size)

    def get_adjustment(self, matchkey_name: str) -> LearnedAdjustment | None:
        row = self._conn.execute(
            "SELECT * FROM adjustments WHERE matchkey_name = ?",
            (matchkey_name,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_adjustment(row)

    def get_all_adjustments(self) -> list[LearnedAdjustment]:
        rows = self._conn.execute("SELECT * FROM adjustments").fetchall()
        return [self._row_to_adjustment(r) for r in rows]

    def last_learn_time(self) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(learned_at) FROM adjustments",
        ).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    @staticmethod
    def _row_to_correction(row: Any) -> Correction:
        # #437: field-level columns are nullable for pair-level rows.
        # `sqlite3.Row` lookup uses `KeyError`-free access via mapping;
        # `.keys()` reflects ALTER TABLE additions on the open connection.
        keys = row.keys() if hasattr(row, "keys") else ()
        return Correction(
            id=row["id"], id_a=row["id_a"], id_b=row["id_b"],
            decision=row["decision"], source=row["source"],
            trust=row["trust"], field_hash=row["field_hash"],
            record_hash=row["record_hash"],
            original_score=row["original_score"],
            matchkey_name=row["matchkey_name"],
            reason=row["reason"], dataset=row["dataset"],
            created_at=datetime.fromisoformat(row["created_at"]),
            field_name=row["field_name"] if "field_name" in keys else None,
            original_value=row["original_value"] if "original_value" in keys else None,
            corrected_value=row["corrected_value"] if "corrected_value" in keys else None,
            # v1.20.x cluster-decision (RFC, 2026-05-22):
            cluster_score=row["cluster_score"] if "cluster_score" in keys else None,
            cluster_outcome=row["cluster_outcome"] if "cluster_outcome" in keys else None,
        )

    @staticmethod
    def _row_to_adjustment(row: Any) -> LearnedAdjustment:
        weights = json.loads(row["field_weights"]) if row["field_weights"] else None
        return LearnedAdjustment(
            matchkey_name=row["matchkey_name"],
            threshold=row["threshold"],
            field_weights=weights,
            sample_size=row["sample_size"],
            learned_at=datetime.fromisoformat(row["learned_at"]),
        )
