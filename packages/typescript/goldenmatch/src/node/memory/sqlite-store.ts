/**
 * sqlite-store.ts -- SqliteMemoryStore (Node-only persistence backend).
 *
 * Ports `goldenmatch/core/memory/store.py:85-249` (the SQLite branch). Schema
 * is byte-identical to Python so a memory.db produced by either toolkit is
 * readable by the other. Pairs are canonicalized to `(min, max)` on insert.
 * Trust upsert: incoming with `trust < existing.trust` is ignored; same-tier
 * overwrites via DELETE + INSERT inside a single transaction (mirrors
 * Python `store.py:152-170`).
 *
 * `better-sqlite3` is loaded as an OPTIONAL peer dep via
 * `await import("better-sqlite3" as string)` -- the `as string` cast prevents
 * tsup from resolving the import at build time so consumers without the dep
 * still get a clean bundle.
 */

import { dirname } from "node:path";
import { mkdirSync } from "node:fs";

import type {
  Correction,
  CorrectionSource,
  Decision,
  LearnedAdjustment,
  MemoryConfig,
  MemoryStore,
} from "../../core/memory/types.js";

// ---------------------------------------------------------------------------
// Schema (byte-identical with Python `_SCHEMA` at store.py:85-105)
// ---------------------------------------------------------------------------

const SCHEMA = `
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    id_a INTEGER, id_b INTEGER,
    decision TEXT, source TEXT, trust REAL,
    field_hash TEXT, record_hash TEXT,
    original_score REAL,
    matchkey_name TEXT,
    reason TEXT, dataset TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- v1.18.2 field-level golden corrections (#437). NULL for pair-
    -- level (decision in {approve, reject}). Set when
    -- decision='field_correct'.
    field_name TEXT,
    original_value TEXT,
    corrected_value TEXT,
    UNIQUE(id_a, id_b, dataset)
);
CREATE INDEX IF NOT EXISTS idx_corrections_pair ON corrections(id_a, id_b, dataset);

CREATE TABLE IF NOT EXISTS adjustments (
    matchkey_name TEXT PRIMARY KEY,
    threshold REAL, field_weights TEXT,
    sample_size INTEGER,
    learned_at TIMESTAMP
);
`;

// ---------------------------------------------------------------------------
// Row shapes (snake_case from SQLite)
// ---------------------------------------------------------------------------

interface CorrectionRow {
  id: string;
  id_a: number;
  id_b: number;
  decision: string;
  source: string;
  trust: number;
  field_hash: string;
  record_hash: string;
  original_score: number;
  matchkey_name: string | null;
  reason: string | null;
  dataset: string | null;
  created_at: string;
  // v1.18.2 field-level (nullable for pair-level corrections):
  field_name?: string | null;
  original_value?: string | null;
  corrected_value?: string | null;
}

interface AdjustmentRow {
  matchkey_name: string;
  threshold: number | null;
  field_weights: string | null;
  sample_size: number;
  learned_at: string;
}

function rowToCorrection(row: CorrectionRow): Correction {
  return {
    id: row.id,
    idA: row.id_a,
    idB: row.id_b,
    decision: row.decision as Decision,
    source: row.source as CorrectionSource,
    trust: row.trust,
    fieldHash: row.field_hash,
    recordHash: row.record_hash,
    originalScore: row.original_score,
    matchkeyName: row.matchkey_name,
    reason: row.reason,
    dataset: row.dataset,
    createdAt: new Date(row.created_at),
    fieldName: row.field_name ?? null,
    originalValue: row.original_value ?? null,
    correctedValue: row.corrected_value ?? null,
  };
}

function rowToAdjustment(row: AdjustmentRow): LearnedAdjustment {
  return {
    matchkeyName: row.matchkey_name,
    threshold: row.threshold,
    fieldWeights: row.field_weights ? (JSON.parse(row.field_weights) as Record<string, number>) : null,
    sampleSize: row.sample_size,
    learnedAt: new Date(row.learned_at),
  };
}

function canonPair(idA: number, idB: number): readonly [number, number] {
  return idA <= idB ? [idA, idB] : [idB, idA];
}

// ---------------------------------------------------------------------------
// SqliteMemoryStore
// ---------------------------------------------------------------------------

export class SqliteMemoryStore implements MemoryStore {
  // Underlying better-sqlite3 Database handle. Typed as `any` because the
  // import is dynamic and the type package is not a hard dependency.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private db: any = null;

  constructor(private readonly config: MemoryConfig & { path: string }) {}

  /**
   * Open the database, ensure the parent directory exists, and apply the
   * schema. Mirrors Python `MemoryStore.__init__` at store.py:111-124.
   */
  async init(): Promise<void> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let mod: any;
    try {
      mod = await import("better-sqlite3" as string);
    } catch {
      throw new Error(
        "better-sqlite3 is required for SqliteMemoryStore. Install it: npm install better-sqlite3",
      );
    }
    const BetterSqlite3 = mod.default ?? mod;
    const dir = dirname(this.config.path);
    if (dir && dir !== ".") {
      mkdirSync(dir, { recursive: true });
    }
    this.db = new BetterSqlite3(this.config.path);
    // Multi-statement DDL via better-sqlite3's schema-runner (mirrors
    // `_conn.executescript(_SCHEMA)` at store.py:123).
    this.db.exec(SCHEMA);
  }

  // -------------------------------------------------------------------------
  // Corrections
  // -------------------------------------------------------------------------

  async addCorrection(c: Correction): Promise<void> {
    const [ca, cb] = canonPair(c.idA, c.idB);
    const existing = await this.getCorrection(ca, cb, c.dataset);
    if (existing !== null && c.trust < existing.trust) {
      // Lower trust ignored (parity with store.py:147-149).
      return;
    }
    // Atomic upsert: DELETE + INSERT inside a transaction. Same shape as
    // Python store.py:152-170; better-sqlite3's `transaction(fn)` wraps the
    // closure in BEGIN/COMMIT and rolls back on throw.
    const del = this.db.prepare(
      "DELETE FROM corrections WHERE id_a = ? AND id_b = ? AND dataset IS ?",
    );
    const ins = this.db.prepare(
      "INSERT INTO corrections " +
        "(id, id_a, id_b, decision, source, trust, field_hash, record_hash, " +
        "original_score, matchkey_name, reason, dataset, created_at, " +
        "field_name, original_value, corrected_value) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    );
    const tx = this.db.transaction((corr: Correction, a: number, b: number) => {
      del.run(a, b, corr.dataset);
      ins.run(
        corr.id,
        a,
        b,
        corr.decision,
        corr.source,
        corr.trust,
        corr.fieldHash,
        corr.recordHash,
        corr.originalScore,
        corr.matchkeyName,
        corr.reason,
        corr.dataset,
        corr.createdAt.toISOString(),
        corr.fieldName ?? null,
        corr.originalValue ?? null,
        corr.correctedValue ?? null,
      );
    });
    tx(c, ca, cb);
  }

  async getCorrection(
    idA: number,
    idB: number,
    dataset: string | null,
  ): Promise<Correction | null> {
    const [ca, cb] = canonPair(idA, idB);
    let row: CorrectionRow | undefined;
    if (dataset !== null) {
      row = this.db
        .prepare("SELECT * FROM corrections WHERE id_a = ? AND id_b = ? AND dataset = ?")
        .get(ca, cb, dataset) as CorrectionRow | undefined;
    } else {
      row = this.db
        .prepare("SELECT * FROM corrections WHERE id_a = ? AND id_b = ? AND dataset IS NULL")
        .get(ca, cb) as CorrectionRow | undefined;
    }
    return row ? rowToCorrection(row) : null;
  }

  async getCorrections(opts?: { dataset?: string | null }): Promise<Correction[]> {
    let rows: CorrectionRow[];
    if (opts === undefined || opts.dataset === undefined) {
      rows = this.db
        .prepare("SELECT * FROM corrections ORDER BY created_at")
        .all() as CorrectionRow[];
    } else if (opts.dataset === null) {
      rows = this.db
        .prepare("SELECT * FROM corrections WHERE dataset IS NULL ORDER BY created_at")
        .all() as CorrectionRow[];
    } else {
      rows = this.db
        .prepare("SELECT * FROM corrections WHERE dataset = ? ORDER BY created_at")
        .all(opts.dataset) as CorrectionRow[];
    }
    return rows.map(rowToCorrection);
  }

  async countCorrections(dataset?: string | null): Promise<number> {
    let row: { c: number };
    if (dataset === undefined) {
      row = this.db.prepare("SELECT COUNT(*) AS c FROM corrections").get() as { c: number };
    } else if (dataset === null) {
      row = this.db
        .prepare("SELECT COUNT(*) AS c FROM corrections WHERE dataset IS NULL")
        .get() as { c: number };
    } else {
      row = this.db
        .prepare("SELECT COUNT(*) AS c FROM corrections WHERE dataset = ?")
        .get(dataset) as { c: number };
    }
    return row.c;
  }

  async correctionsSince(since: Date): Promise<Correction[]> {
    const rows = this.db
      .prepare("SELECT * FROM corrections WHERE created_at > ? ORDER BY created_at")
      .all(since.toISOString()) as CorrectionRow[];
    return rows.map(rowToCorrection);
  }

  // -------------------------------------------------------------------------
  // Adjustments
  // -------------------------------------------------------------------------

  async saveAdjustment(a: LearnedAdjustment): Promise<void> {
    const weightsJson = a.fieldWeights ? JSON.stringify(a.fieldWeights) : null;
    this.db
      .prepare(
        "INSERT OR REPLACE INTO adjustments " +
          "(matchkey_name, threshold, field_weights, sample_size, learned_at) " +
          "VALUES (?, ?, ?, ?, ?)",
      )
      .run(a.matchkeyName, a.threshold, weightsJson, a.sampleSize, a.learnedAt.toISOString());
  }

  async getAdjustment(matchkeyName: string): Promise<LearnedAdjustment | null> {
    const row = this.db
      .prepare("SELECT * FROM adjustments WHERE matchkey_name = ?")
      .get(matchkeyName) as AdjustmentRow | undefined;
    return row ? rowToAdjustment(row) : null;
  }

  async getAllAdjustments(): Promise<LearnedAdjustment[]> {
    const rows = this.db.prepare("SELECT * FROM adjustments").all() as AdjustmentRow[];
    return rows.map(rowToAdjustment);
  }

  async lastLearnTime(): Promise<Date | null> {
    const row = this.db.prepare("SELECT MAX(learned_at) AS m FROM adjustments").get() as {
      m: string | null;
    };
    return row.m ? new Date(row.m) : null;
  }

  async close(): Promise<void> {
    this.db?.close();
    this.db = null;
  }
}
