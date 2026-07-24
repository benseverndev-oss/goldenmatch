/**
 * sqlite-store.ts -- SqliteIdentityStore (Node-only persistence backend).
 *
 * Ports goldenmatch/identity/store.py:126-635 (the SQLite branch). Schema
 * is byte-identical to Python so an identity.db produced by either toolkit
 * is readable by the other. Record pairs are canonicalized to (min, max)
 * on insert (mirrors canon_record_pair). Schema version 2 migration from
 * Python is preserved verbatim.
 *
 * better-sqlite3 is loaded as an optional peer dep via dynamic import --
 * same pattern as src/node/memory/sqlite-store.ts.
 */

import { mkdirSync } from "node:fs";
import { dirname, normalize } from "node:path";

import {
  canonRecordPair,
  type AuditSeal,
  type ClaimType,
  type EdgeKind,
  type EventKind,
  type EvidenceRef,
  type EvidenceEdge,
  type IdentityAlias,
  type IdentityEvent,
  type IdentityNode,
  type IdentityStatus,
  type IdentityStore,
  type SourceRecord,
} from "../../core/identity/types.js";
import { pyIsoformat } from "../../core/identity/pyDatetime.js";
import { eventContentHash } from "../../core/identity/audit.js";

// v5 (byte-identical to Python `store.py` SCHEMA_VERSION 5): the provenance
// spine (#1075 actor/trust on events + edges), tamper-evidence (#1078
// entry_hash + the audit_seals chain table), and the claim-authority tier
// (#1256 claim_type/evidence_ref/previous_claim_id). A DB written by either
// toolkit is schema-identical.
const SCHEMA_VERSION = 5;

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS identity_nodes (
    entity_id      TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'active',
    merged_into    TEXT,
    golden_record  TEXT,
    confidence     REAL,
    dataset        TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );`,
  `CREATE INDEX IF NOT EXISTS idx_identity_nodes_dataset ON identity_nodes(dataset);`,
  `CREATE INDEX IF NOT EXISTS idx_identity_nodes_status  ON identity_nodes(status);`,
  `CREATE TABLE IF NOT EXISTS source_records (
    record_id      TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_pk      TEXT NOT NULL,
    record_hash    TEXT NOT NULL,
    entity_id      TEXT,
    payload        TEXT,
    dataset        TEXT,
    first_seen_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES identity_nodes(entity_id) ON DELETE SET NULL
  );`,
  `CREATE INDEX IF NOT EXISTS idx_source_records_entity ON source_records(entity_id);`,
  `CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source);`,
  `CREATE INDEX IF NOT EXISTS idx_source_records_hash   ON source_records(record_hash);`,
  `CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id            TEXT NOT NULL,
    record_a_id          TEXT NOT NULL,
    record_b_id          TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'same_as',
    score                REAL,
    matchkey_name        TEXT,
    field_scores         TEXT,
    negative_evidence    TEXT,
    controller_snapshot  TEXT,
    run_name             TEXT,
    dataset              TEXT,
    actor                TEXT,
    trust                REAL,
    recorded_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
  );`,
  `CREATE INDEX IF NOT EXISTS idx_edges_entity ON evidence_edges(entity_id);`,
  `CREATE INDEX IF NOT EXISTS idx_edges_pair   ON evidence_edges(record_a_id, record_b_id);`,
  `CREATE INDEX IF NOT EXISTS idx_edges_run    ON evidence_edges(run_name);`,
  `CREATE TABLE IF NOT EXISTS identity_events (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    payload           TEXT,
    run_name          TEXT,
    dataset           TEXT,
    actor             TEXT,
    trust             REAL,
    claim_type        TEXT,
    evidence_ref      TEXT,
    previous_claim_id INTEGER,
    entry_hash        TEXT,
    recorded_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );`,
  `CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);`,
  `CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);`,
  `CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);`,
  `CREATE TABLE IF NOT EXISTS audit_seals (
    seal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT,
    root_hash     TEXT NOT NULL,
    event_count   INTEGER NOT NULL,
    last_event_id INTEGER,
    prev_seal_id  INTEGER,
    prev_root     TEXT,
    actor         TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );`,
  `CREATE INDEX IF NOT EXISTS idx_audit_seals_dataset ON audit_seals(dataset);`,
  `CREATE TABLE IF NOT EXISTS identity_aliases (
    alias        TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'external_id',
    dataset      TEXT,
    recorded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alias, kind, dataset)
  );`,
  `CREATE INDEX IF NOT EXISTS idx_aliases_entity ON identity_aliases(entity_id);`,
].join("\n");

interface IdentityRow {
  entity_id: string;
  status: string;
  merged_into: string | null;
  golden_record: string | null;
  confidence: number | null;
  dataset: string | null;
  created_at: string;
  updated_at: string;
}

interface RecordRow {
  record_id: string;
  source: string;
  source_pk: string;
  record_hash: string;
  entity_id: string | null;
  payload: string | null;
  dataset: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

interface EdgeRow {
  edge_id: number;
  entity_id: string;
  record_a_id: string;
  record_b_id: string;
  kind: string;
  score: number | null;
  matchkey_name: string | null;
  field_scores: string | null;
  negative_evidence: string | null;
  controller_snapshot: string | null;
  run_name: string | null;
  dataset: string | null;
  actor: string | null;
  trust: number | null;
  recorded_at: string;
}

interface EventRow {
  event_id: number;
  entity_id: string;
  kind: string;
  payload: string | null;
  run_name: string | null;
  dataset: string | null;
  actor: string | null;
  trust: number | null;
  claim_type: string | null;
  evidence_ref: string | null;
  previous_claim_id: number | null;
  entry_hash: string | null;
  recorded_at: string;
}

interface SealRow {
  seal_id: number;
  dataset: string | null;
  root_hash: string;
  event_count: number;
  last_event_id: number | null;
  prev_seal_id: number | null;
  prev_root: string | null;
  actor: string | null;
  created_at: string;
}

export interface SqliteIdentityStoreOptions {
  readonly path?: string;
}

// Columns each v5 table gained since v2, with their SQLite type. Order matches
// Python `store.py` (_ensure_provenance_columns / _ensure_audit_columns /
// _ensure_claim_columns) so a migrated DB is column-identical to a fresh one.
const V5_ADDED_COLUMNS: Readonly<Record<string, ReadonlyArray<readonly [string, string]>>> = {
  identity_events: [
    ["actor", "TEXT"],
    ["trust", "REAL"],
    ["claim_type", "TEXT"],
    ["evidence_ref", "TEXT"],
    ["previous_claim_id", "INTEGER"],
    ["entry_hash", "TEXT"],
  ],
  evidence_edges: [
    ["actor", "TEXT"],
    ["trust", "REAL"],
  ],
};

/**
 * Idempotent v2 -> v5 migration. Mirrors Python `IdentityStore._migrate`'s
 * PRAGMA-guarded probes: for each table, `PRAGMA table_info` is read and a
 * missing column is added via `ALTER TABLE ... ADD COLUMN` -- NEVER blindly
 * (re-adding an existing column errors). So this is a real no-op on a
 * Python-written v5 DB (every column already present) and adds only the absent
 * columns on an old v2 DB (new columns read back NULL). `audit_seals` is
 * created by `CREATE TABLE IF NOT EXISTS` in SCHEMA, so no explicit create here.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function migrateToV5(db: any): void {
  for (const [table, cols] of Object.entries(V5_ADDED_COLUMNS)) {
    const existing = new Set<string>(
      (db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[]).map(
        (r) => r.name,
      ),
    );
    for (const [name, type] of cols) {
      if (!existing.has(name)) {
        db.exec(`ALTER TABLE ${table} ADD COLUMN ${name} ${type}`);
      }
    }
  }
}

export class SqliteIdentityStore implements IdentityStore {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private db: any;

  private constructor(db: unknown) {
    this.db = db;
  }

  static async open(
    options: SqliteIdentityStoreOptions = {},
  ): Promise<SqliteIdentityStore> {
    const path = options.path ?? ".goldenmatch/identity.db";
    const safePath = normalize(path);
    const parent = dirname(safePath) || ".";
    mkdirSync(parent, { recursive: true });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let Database: any;
    try {
      const mod = await import("better-sqlite3" as string);
      Database = (mod as { default?: unknown }).default ?? mod;
    } catch (e) {
      throw new Error(
        "SqliteIdentityStore requires the 'better-sqlite3' optional peer dep. " +
          `Install: npm install better-sqlite3. Underlying: ${
            e instanceof Error ? e.message : String(e)
          }`,
      );
    }
    const db = new Database(safePath, { timeout: 30000 });
    db.pragma("journal_mode = WAL");
    db.pragma("busy_timeout = 5000");
    db.pragma("foreign_keys = ON");
    // `CREATE TABLE IF NOT EXISTS` (re)creates only ABSENT tables at the v5 shape;
    // a pre-existing table keeps its old columns, so the real work is the
    // idempotent ADD COLUMN migration below (mirrors Python `_migrate`).
    db.exec(SCHEMA);
    migrateToV5(db);
    const row = db.prepare("PRAGMA user_version").get() as { user_version: number };
    const version = row?.user_version ?? 0;
    if (version < SCHEMA_VERSION) {
      db.pragma(`user_version = ${SCHEMA_VERSION}`);
    }
    return new SqliteIdentityStore(db);
  }

  async upsertIdentity(node: IdentityNode): Promise<void> {
    const gr = node.goldenRecord !== null ? JSON.stringify(node.goldenRecord) : null;
    this.db
      .prepare(
        `INSERT INTO identity_nodes
           (entity_id, status, merged_into, golden_record, confidence, dataset,
            created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(entity_id) DO UPDATE SET
           status=excluded.status,
           merged_into=excluded.merged_into,
           golden_record=excluded.golden_record,
           confidence=excluded.confidence,
           dataset=excluded.dataset,
           updated_at=excluded.updated_at`,
      )
      .run(
        node.entityId,
        node.status,
        node.mergedInto,
        gr,
        node.confidence,
        node.dataset,
        node.createdAt.toISOString(),
        node.updatedAt.toISOString(),
      );
  }

  async getIdentity(entityId: string): Promise<IdentityNode | null> {
    const row = this.db
      .prepare("SELECT * FROM identity_nodes WHERE entity_id = ?")
      .get(entityId) as IdentityRow | undefined;
    return row ? rowToIdentity(row) : null;
  }

  async listIdentities(
    opts: {
      dataset?: string;
      status?: IdentityStatus;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<IdentityNode[]> {
    const where: string[] = [];
    const params: (string | number)[] = [];
    if (opts.dataset !== undefined) {
      where.push("dataset = ?");
      params.push(opts.dataset);
    }
    if (opts.status !== undefined) {
      where.push("status = ?");
      params.push(opts.status);
    }
    const whereSql = where.length > 0 ? ` WHERE ${where.join(" AND ")}` : "";
    const limit = opts.limit ?? 100;
    const offset = opts.offset ?? 0;
    params.push(limit, offset);
    const rows = this.db
      .prepare(
        `SELECT * FROM identity_nodes${whereSql} ORDER BY updated_at DESC LIMIT ? OFFSET ?`,
      )
      .all(...params) as IdentityRow[];
    return rows.map(rowToIdentity);
  }

  async countIdentities(dataset?: string): Promise<number> {
    if (dataset === undefined) {
      const row = this.db
        .prepare("SELECT COUNT(*) AS n FROM identity_nodes")
        .get() as { n: number };
      return row.n;
    }
    const row = this.db
      .prepare("SELECT COUNT(*) AS n FROM identity_nodes WHERE dataset = ?")
      .get(dataset) as { n: number };
    return row.n;
  }

  async retireIdentity(entityId: string, mergedInto?: string): Promise<void> {
    const newStatus: IdentityStatus =
      mergedInto !== undefined ? "merged_into" : "retired";
    this.db
      .prepare(
        "UPDATE identity_nodes SET status = ?, merged_into = ?, updated_at = ? WHERE entity_id = ?",
      )
      .run(newStatus, mergedInto ?? null, new Date().toISOString(), entityId);
  }

  async upsertRecord(rec: SourceRecord): Promise<void> {
    const payload = rec.payload !== null ? JSON.stringify(rec.payload) : null;
    this.db
      .prepare(
        `INSERT INTO source_records
           (record_id, source, source_pk, record_hash, entity_id, payload,
            dataset, first_seen_at, last_seen_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(record_id) DO UPDATE SET
           record_hash=excluded.record_hash,
           entity_id=excluded.entity_id,
           payload=excluded.payload,
           last_seen_at=excluded.last_seen_at`,
      )
      .run(
        rec.recordId,
        rec.source,
        rec.sourcePk,
        rec.recordHash,
        rec.entityId,
        payload,
        rec.dataset,
        rec.firstSeenAt.toISOString(),
        rec.lastSeenAt.toISOString(),
      );
  }

  async getRecord(recordId: string): Promise<SourceRecord | null> {
    const row = this.db
      .prepare("SELECT * FROM source_records WHERE record_id = ?")
      .get(recordId) as RecordRow | undefined;
    return row ? rowToRecord(row) : null;
  }

  async getRecordsForEntity(entityId: string): Promise<SourceRecord[]> {
    const rows = this.db
      .prepare(
        "SELECT * FROM source_records WHERE entity_id = ? ORDER BY first_seen_at",
      )
      .all(entityId) as RecordRow[];
    return rows.map(rowToRecord);
  }

  async findEntityByRecord(recordId: string): Promise<string | null> {
    const row = this.db
      .prepare("SELECT entity_id FROM source_records WHERE record_id = ?")
      .get(recordId) as { entity_id: string | null } | undefined;
    return row?.entity_id ?? null;
  }

  async lookupEntityIds(
    recordIds: readonly string[],
  ): Promise<Map<string, string>> {
    const out = new Map<string, string>();
    if (recordIds.length === 0) return out;
    const placeholders = recordIds.map(() => "?").join(",");
    const rows = this.db
      .prepare(
        `SELECT record_id, entity_id FROM source_records WHERE record_id IN (${placeholders}) AND entity_id IS NOT NULL`,
      )
      .all(...recordIds) as { record_id: string; entity_id: string }[];
    for (const r of rows) out.set(r.record_id, r.entity_id);
    return out;
  }

  async addEdge(edge: EvidenceEdge): Promise<number | null> {
    const [a, b] = canonRecordPair(edge.recordAId, edge.recordBId);
    const fs = edge.fieldScores ? JSON.stringify(edge.fieldScores) : null;
    const ne = edge.negativeEvidence ? JSON.stringify(edge.negativeEvidence) : null;
    const cs = edge.controllerSnapshot
      ? JSON.stringify(edge.controllerSnapshot)
      : null;
    this.db
      .prepare(
        `INSERT OR IGNORE INTO evidence_edges
           (entity_id, record_a_id, record_b_id, kind, score, matchkey_name,
            field_scores, negative_evidence, controller_snapshot, run_name,
            dataset, actor, trust, recorded_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        edge.entityId,
        a,
        b,
        edge.kind,
        edge.score,
        edge.matchkeyName,
        fs,
        ne,
        cs,
        edge.runName,
        edge.dataset,
        edge.actor ?? null,
        edge.trust ?? null,
        edge.recordedAt.toISOString(),
      );
    const row = this.db
      .prepare(
        "SELECT edge_id FROM evidence_edges WHERE entity_id=? AND record_a_id=? AND record_b_id=? AND kind=? AND COALESCE(run_name,'')=COALESCE(?,'')",
      )
      .get(edge.entityId, a, b, edge.kind, edge.runName) as
      | { edge_id: number }
      | undefined;
    return row ? row.edge_id : null;
  }

  async edgesForEntity(entityId: string): Promise<EvidenceEdge[]> {
    const rows = this.db
      .prepare(
        "SELECT * FROM evidence_edges WHERE entity_id = ? ORDER BY recorded_at",
      )
      .all(entityId) as EdgeRow[];
    return rows.map(rowToEdge);
  }

  async edgesByKind(kind: string, dataset?: string): Promise<EvidenceEdge[]> {
    // recorded_at DESC then edge_id DESC so the latest edge for a pair wins
    // deterministically when timestamps tie (mirrors the in-memory store).
    const rows: EdgeRow[] =
      dataset === undefined
        ? (this.db
            .prepare(
              "SELECT * FROM evidence_edges WHERE kind = ? ORDER BY recorded_at DESC, edge_id DESC",
            )
            .all(kind) as EdgeRow[])
        : (this.db
            .prepare(
              "SELECT * FROM evidence_edges WHERE kind = ? AND dataset = ? ORDER BY recorded_at DESC, edge_id DESC",
            )
            .all(kind, dataset) as EdgeRow[]);
    return rows.map(rowToEdge);
  }

  async findConflicts(dataset?: string): Promise<EvidenceEdge[]> {
    const rows: EdgeRow[] =
      dataset === undefined
        ? (this.db
            .prepare(
              "SELECT * FROM evidence_edges WHERE kind = 'conflicts_with' ORDER BY recorded_at DESC",
            )
            .all() as EdgeRow[])
        : (this.db
            .prepare(
              "SELECT * FROM evidence_edges WHERE kind = 'conflicts_with' AND dataset = ? ORDER BY recorded_at DESC",
            )
            .all(dataset) as EdgeRow[]);
    return rows.map(rowToEdge);
  }

  async emitEvent(event: IdentityEvent): Promise<number | null> {
    const payload = event.payload !== null ? JSON.stringify(event.payload) : null;
    // Stamp the tamper-evidence content hash at insert (PR-B), mirroring Python
    // `store.emit_event`. Only when absent, so a hydrated event keeps its hash.
    const entryHash = event.entryHash ?? (await eventContentHash(event));
    this.db
      .prepare(
        `INSERT INTO identity_events
           (entity_id, kind, payload, run_name, dataset, actor, trust,
            claim_type, evidence_ref, previous_claim_id, entry_hash, recorded_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        event.entityId,
        event.kind,
        payload,
        event.runName,
        event.dataset,
        event.actor ?? null,
        event.trust ?? null,
        event.claimType ?? null,
        event.evidenceRef ?? null,
        event.previousClaimId ?? null,
        entryHash,
        // Python stamps `recorded_at` via datetime.isoformat(); match it byte-for
        // -byte so the stored string hashes identically cross-toolkit (PR-B).
        pyIsoformat(event.recordedAt),
      );
    const row = this.db
      .prepare(
        "SELECT MAX(event_id) AS event_id FROM identity_events WHERE entity_id = ?",
      )
      .get(event.entityId) as { event_id: number | null };
    return row?.event_id ?? null;
  }

  async history(entityId: string, limit?: number): Promise<IdentityEvent[]> {
    const rows: EventRow[] = limit
      ? (this.db
          .prepare(
            "SELECT * FROM identity_events WHERE entity_id = ? ORDER BY event_id LIMIT ?",
          )
          .all(entityId, limit) as EventRow[])
      : (this.db
          .prepare(
            "SELECT * FROM identity_events WHERE entity_id = ? ORDER BY event_id",
          )
          .all(entityId) as EventRow[]);
    return rows.map(rowToEvent);
  }

  async hasRunEvent(
    entityId: string,
    runName: string,
    kind: EventKind,
  ): Promise<boolean> {
    const row = this.db
      .prepare(
        "SELECT 1 AS one FROM identity_events WHERE entity_id = ? AND run_name = ? AND kind = ? LIMIT 1",
      )
      .get(entityId, runName, kind);
    return row !== undefined;
  }

  async exportAuditLog(dataset?: string): Promise<IdentityEvent[]> {
    // `dataset` scopes to a dataset; omitted = the whole log (Python's
    // export_audit_log with no filter). ORDER BY event_id = commit order.
    const rows: EventRow[] =
      dataset === undefined
        ? (this.db
            .prepare("SELECT * FROM identity_events ORDER BY event_id")
            .all() as EventRow[])
        : (this.db
            .prepare(
              "SELECT * FROM identity_events WHERE dataset = ? ORDER BY event_id",
            )
            .all(dataset) as EventRow[]);
    return rows.map(rowToEvent);
  }

  async addSeal(seal: AuditSeal): Promise<number | null> {
    this.db
      .prepare(
        `INSERT INTO audit_seals
           (dataset, root_hash, event_count, last_event_id, prev_seal_id,
            prev_root, actor, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        seal.dataset,
        seal.rootHash,
        seal.eventCount,
        seal.lastEventId,
        seal.prevSealId,
        seal.prevRoot,
        seal.actor,
        pyIsoformat(seal.createdAt),
      );
    const row = this.db
      .prepare("SELECT MAX(seal_id) AS seal_id FROM audit_seals")
      .get() as { seal_id: number | null };
    return row?.seal_id ?? null;
  }

  async latestSeal(dataset?: string): Promise<AuditSeal | null> {
    // dataset === undefined => the global chain (`dataset IS NULL`), mirroring
    // Python's `dataset=None` default.
    const row: SealRow | undefined =
      dataset === undefined
        ? (this.db
            .prepare(
              "SELECT * FROM audit_seals WHERE dataset IS NULL ORDER BY seal_id DESC LIMIT 1",
            )
            .get() as SealRow | undefined)
        : (this.db
            .prepare(
              "SELECT * FROM audit_seals WHERE dataset = ? ORDER BY seal_id DESC LIMIT 1",
            )
            .get(dataset) as SealRow | undefined);
    return row ? rowToSeal(row) : null;
  }

  async listSeals(dataset?: string): Promise<AuditSeal[]> {
    const rows: SealRow[] =
      dataset === undefined
        ? (this.db
            .prepare("SELECT * FROM audit_seals WHERE dataset IS NULL ORDER BY seal_id")
            .all() as SealRow[])
        : (this.db
            .prepare("SELECT * FROM audit_seals WHERE dataset = ? ORDER BY seal_id")
            .all(dataset) as SealRow[]);
    return rows.map(rowToSeal);
  }

  async addAlias(alias: IdentityAlias): Promise<void> {
    this.db
      .prepare(
        "INSERT OR REPLACE INTO identity_aliases (alias, entity_id, kind, dataset, recorded_at) VALUES (?, ?, ?, ?, ?)",
      )
      .run(
        alias.alias,
        alias.entityId,
        alias.kind,
        alias.dataset,
        alias.recordedAt.toISOString(),
      );
  }

  async resolveAlias(alias: string, kind = "external_id"): Promise<string | null> {
    const row = this.db
      .prepare(
        "SELECT entity_id FROM identity_aliases WHERE alias = ? AND kind = ?",
      )
      .get(alias, kind) as { entity_id: string } | undefined;
    return row?.entity_id ?? null;
  }

  async close(): Promise<void> {
    this.db.close();
  }
}

function rowToIdentity(row: IdentityRow): IdentityNode {
  return {
    entityId: row.entity_id,
    status: row.status as IdentityStatus,
    mergedInto: row.merged_into,
    goldenRecord: parseJsonOrNull(row.golden_record),
    confidence: row.confidence,
    dataset: row.dataset,
    createdAt: parseDate(row.created_at),
    updatedAt: parseDate(row.updated_at),
  };
}

function rowToRecord(row: RecordRow): SourceRecord {
  return {
    recordId: row.record_id,
    source: row.source,
    sourcePk: row.source_pk,
    recordHash: row.record_hash,
    entityId: row.entity_id,
    payload: parseJsonOrNull(row.payload),
    dataset: row.dataset,
    firstSeenAt: parseDate(row.first_seen_at),
    lastSeenAt: parseDate(row.last_seen_at),
  };
}

function rowToEdge(row: EdgeRow): EvidenceEdge {
  return {
    edgeId: row.edge_id,
    entityId: row.entity_id,
    recordAId: row.record_a_id,
    recordBId: row.record_b_id,
    kind: row.kind as EdgeKind,
    score: row.score,
    matchkeyName: row.matchkey_name,
    fieldScores: parseJsonOrNull(row.field_scores),
    negativeEvidence: parseJsonOrNull(row.negative_evidence),
    controllerSnapshot: parseJsonOrNull(row.controller_snapshot),
    runName: row.run_name,
    dataset: row.dataset,
    actor: row.actor,
    trust: row.trust,
    recordedAt: parseDate(row.recorded_at),
  };
}

function rowToEvent(row: EventRow): IdentityEvent {
  return {
    eventId: row.event_id,
    entityId: row.entity_id,
    kind: row.kind as EventKind,
    payload: parseJsonOrNull(row.payload),
    runName: row.run_name,
    dataset: row.dataset,
    actor: row.actor,
    trust: row.trust,
    claimType: row.claim_type as ClaimType | null,
    evidenceRef: row.evidence_ref as EvidenceRef | null,
    previousClaimId: row.previous_claim_id,
    entryHash: row.entry_hash,
    recordedAt: parseDate(row.recorded_at),
  };
}

function rowToSeal(row: SealRow): AuditSeal {
  return {
    sealId: row.seal_id,
    dataset: row.dataset,
    rootHash: row.root_hash,
    eventCount: row.event_count,
    lastEventId: row.last_event_id,
    prevSealId: row.prev_seal_id,
    prevRoot: row.prev_root,
    actor: row.actor,
    createdAt: parseDate(row.created_at),
  };
}

function parseJsonOrNull(v: string | null): Record<string, unknown> | null {
  if (v === null || v === "") return null;
  try {
    return JSON.parse(v) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function parseDate(v: string): Date {
  let normalised = v.includes(" ") && !v.includes("T") ? v.replace(" ", "T") : v;
  // `identity_events.recorded_at` is written via `pyIsoformat` (naive, no zone)
  // + SQLite's CURRENT_TIMESTAMP default is naive-UTC; a naive `new Date(...)`
  // parses as LOCAL, so a read-back event re-hashed by the audit layer would
  // diverge from its stored entry_hash on any non-UTC machine (false tamper).
  // Force UTC for naive strings; already-zoned values (edges/aliases use
  // `toISOString()`, which carries `Z`) are left untouched.
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(normalised)) normalised += "Z";
  return new Date(normalised);
}
