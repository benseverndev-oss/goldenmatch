// Node-only database provider. Dispatches to the right driver via dynamic
// import so optional peerDeps (better-sqlite3, pg, @duckdb/node-api) can be
// absent without breaking the package build.
//
// Each driver implementation is small and mirrors the Python provider in
// infermap/providers/db.py. We return the same FieldInfo shape + source_name
// convention so downstream engine behavior matches Python.
import type { FieldInfo, SchemaInfo } from "../../core/types.js";
import { makeFieldInfo, makeSchemaInfo } from "../../core/types.js";
import {
  DbError,
  parseConnection,
  pgTypeToInfermap,
  sqliteTypeToInfermap,
  duckdbTypeToInfermap,
  type ConnInfo,
} from "./types.js";

const DEFAULT_SAMPLE_SIZE = 500;

export interface DbExtractOptions {
  table: string;
  sampleSize?: number;
}

/** Extract a SchemaInfo from a database table by URI + table name. */
export async function extractDbSchema(
  uri: string,
  options: DbExtractOptions
): Promise<SchemaInfo> {
  const conn = parseConnection(uri);
  const sampleSize = options.sampleSize ?? DEFAULT_SAMPLE_SIZE;

  if (conn.driver === "sqlite") {
    return extractSqlite(conn.path, options.table, sampleSize);
  }
  if (conn.driver === "postgresql") {
    return extractPostgres(conn, options.table, sampleSize);
  }
  if (conn.driver === "duckdb") {
    return extractDuckdb(conn.path, options.table, sampleSize);
  }
  if (conn.driver === "mysql") {
    throw new DbError("MySQL support is not implemented");
  }
  throw new DbError(`Unsupported driver: ${(conn as ConnInfo).driver}`);
}

// ---------- SQLite (better-sqlite3) ----------

async function loadBetterSqlite(): Promise<unknown> {
  try {
    // Module not statically typed — it's an optional peerDep, so we
    // intentionally use a non-literal import specifier to bypass TS resolution.
    const mod: { default: unknown } = await import(
      /* @vite-ignore */ "better-sqlite3" as string
    );
    return mod.default;
  } catch {
    throw new DbError(
      "better-sqlite3 is required for SQLite. Install it: npm install better-sqlite3"
    );
  }
}

async function extractSqlite(
  path: string,
  table: string,
  sampleSize: number
): Promise<SchemaInfo> {
  const Database = (await loadBetterSqlite()) as new (
    path: string,
    options?: { readonly?: boolean }
  ) => {
    prepare(sql: string): {
      all(...params: unknown[]): unknown[];
      get(...params: unknown[]): unknown;
    };
    close(): void;
  };

  let db;
  try {
    db = new Database(path, { readonly: true });
  } catch (e) {
    throw new DbError(
      `Cannot connect to SQLite database at ${path}: ${String(e)}`
    );
  }

  try {
    const exists = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=?")
      .get(table);
    if (!exists) {
      throw new DbError(`Table '${table}' not found in SQLite database: ${path}`);
    }

    type PragmaRow = { cid: number; name: string; type: string };
    const pragma = db.prepare(`PRAGMA table_info(${table})`).all() as PragmaRow[];

    const quoted = `"${table.replace(/"/g, '""')}"`;
    const totalRow = db.prepare(`SELECT COUNT(*) AS c FROM ${quoted}`).get() as {
      c: number;
    };
    const total = Number(totalRow.c);

    const sampleRows = db
      .prepare(`SELECT * FROM ${quoted} LIMIT ?`)
      .all(sampleSize) as Array<Record<string, unknown>>;

    const fields: FieldInfo[] = [];
    for (const col of pragma) {
      const nullRow = db
        .prepare(
          `SELECT COUNT(*) AS c FROM ${quoted} WHERE "${col.name.replace(/"/g, '""')}" IS NULL`
        )
        .get() as { c: number };
      const nullCount = Number(nullRow.c);
      const nullRate = total > 0 ? nullCount / total : 0;

      const rawSamples: unknown[] = [];
      for (const row of sampleRows) {
        const v = row[col.name];
        if (v !== null && v !== undefined) rawSamples.push(v);
      }
      const sampleValues = rawSamples
        .slice(0, sampleSize)
        .map((v) => String(v));
      const unique = new Set(rawSamples.map((v) => String(v))).size;
      const uniqueRate = total > 0 ? unique / total : 0;

      fields.push(
        makeFieldInfo({
          name: col.name,
          dtype: sqliteTypeToInfermap(col.type),
          sampleValues,
          nullRate,
          uniqueRate,
          valueCount: total,
          metadata: { db_type: col.type },
        })
      );
    }

    return makeSchemaInfo({ fields, sourceName: table });
  } finally {
    db.close();
  }
}

// ---------- PostgreSQL (pg) ----------

async function loadPg(): Promise<unknown> {
  try {
    return await import(/* @vite-ignore */ "pg" as string);
  } catch {
    throw new DbError(
      "pg is required for PostgreSQL. Install it: npm install pg"
    );
  }
}

async function extractPostgres(
  conn: Extract<ConnInfo, { driver: "postgresql" }>,
  table: string,
  sampleSize: number
): Promise<SchemaInfo> {
  const pg = await loadPg();
  const ClientCtor = (pg as { Client: new (cfg: object) => unknown }).Client;
  type PgClient = {
    connect(): Promise<void>;
    query<T = unknown>(
      text: string,
      values?: unknown[]
    ): Promise<{ rows: T[] }>;
    end(): Promise<void>;
  };
  const client = new ClientCtor({
    host: conn.host,
    port: conn.port,
    user: conn.user,
    password: conn.password,
    database: conn.database,
  }) as PgClient;

  try {
    await client.connect();
  } catch (e) {
    throw new DbError(
      `Cannot connect to PostgreSQL at ${conn.host}:${conn.port}: ${String(e)}`
    );
  }

  try {
    const cols = await client.query<{
      column_name: string;
      data_type: string;
    }>(
      `SELECT column_name, data_type
       FROM information_schema.columns
       WHERE table_name = $1
       ORDER BY ordinal_position`,
      [table]
    );
    if (cols.rows.length === 0) {
      throw new DbError(`Table '${table}' not found or has no columns`);
    }

    const quoted = `"${table.replace(/"/g, '""')}"`;
    const totalRes = await client.query<{ count: string }>(
      `SELECT COUNT(*)::text AS count FROM ${quoted}`
    );
    const total = Number(totalRes.rows[0]!.count);

    const sampleRes = await client.query<Record<string, unknown>>(
      `SELECT * FROM ${quoted} LIMIT $1`,
      [sampleSize]
    );
    const sampleRows = sampleRes.rows;

    const fields: FieldInfo[] = cols.rows.map((col) => {
      const rawSamples: unknown[] = [];
      let nulls = 0;
      for (const row of sampleRows) {
        const v = row[col.column_name];
        if (v === null || v === undefined) nulls++;
        else rawSamples.push(v);
      }
      const totalSampled = sampleRows.length;
      const nullRateRaw = totalSampled > 0 ? nulls / totalSampled : 0;
      const nullRate = Math.round(nullRateRaw * 10000) / 10000;

      const uniqueStrings = new Set(rawSamples.map((v) => String(v)));
      const uniqueRateRaw =
        rawSamples.length > 0 ? uniqueStrings.size / rawSamples.length : 0;
      const uniqueRate = Math.round(uniqueRateRaw * 10000) / 10000;

      return makeFieldInfo({
        name: col.column_name,
        dtype: pgTypeToInfermap(col.data_type),
        sampleValues: rawSamples.slice(0, sampleSize).map((v) => String(v)),
        nullRate,
        uniqueRate,
        // Match the Python quirk: value_count = total - int(null_rate * total)
        valueCount: total - Math.trunc(nullRate * total),
        metadata: { db_type: col.data_type },
      });
    });

    return makeSchemaInfo({
      fields,
      sourceName: `${conn.database}.${table}`,
    });
  } finally {
    await client.end();
  }
}

// ---------- DuckDB (@duckdb/node-api) ----------

async function loadDuckdb(): Promise<unknown> {
  try {
    return await import(/* @vite-ignore */ "@duckdb/node-api" as string);
  } catch {
    throw new DbError(
      "@duckdb/node-api is required for DuckDB. Install it: npm install @duckdb/node-api"
    );
  }
}

async function extractDuckdb(
  path: string,
  table: string,
  sampleSize: number
): Promise<SchemaInfo> {
  const duckdb = await loadDuckdb();
  type DuckInstance = {
    connect(): Promise<DuckConnection>;
    closeSync?(): void;
  };
  type DuckConnection = {
    runAndReadAll(sql: string, params?: unknown[]): Promise<{
      getRows(): unknown[][];
      getRowObjects(): Array<Record<string, unknown>>;
    }>;
    closeSync?(): void;
  };
  const api = duckdb as {
    DuckDBInstance: { create(path: string): Promise<DuckInstance> };
  };

  const dbPath = path || ":memory:";
  const instance = await api.DuckDBInstance.create(dbPath);
  const conn = await instance.connect();

  try {
    const colsReader = await conn.runAndReadAll(
      `SELECT column_name, data_type FROM information_schema.columns
       WHERE table_name = ? ORDER BY ordinal_position`,
      [table]
    );
    const cols = colsReader.getRowObjects() as Array<{
      column_name: string;
      data_type: string;
    }>;
    if (cols.length === 0) {
      throw new DbError(`Table '${table}' not found in DuckDB: ${dbPath}`);
    }

    const quoted = `"${table.replace(/"/g, '""')}"`;
    const totalReader = await conn.runAndReadAll(
      `SELECT COUNT(*)::BIGINT AS c FROM ${quoted}`
    );
    const totalRow = totalReader.getRowObjects()[0] as { c: bigint | number };
    const total = Number(totalRow.c);

    const sampleReader = await conn.runAndReadAll(
      `SELECT * FROM ${quoted} USING SAMPLE ${sampleSize}`
    );
    const sampleRows = sampleReader.getRowObjects() as Array<
      Record<string, unknown>
    >;

    const fields: FieldInfo[] = cols.map((col) => {
      const rawSamples: unknown[] = [];
      let nulls = 0;
      for (const row of sampleRows) {
        const v = row[col.column_name];
        if (v === null || v === undefined) nulls++;
        else rawSamples.push(v);
      }
      const totalSampled = sampleRows.length;
      const nullRateRaw = totalSampled > 0 ? nulls / totalSampled : 0;
      const nullRate = Math.round(nullRateRaw * 10000) / 10000;

      const uniqueStrings = new Set(rawSamples.map((v) => String(v)));
      const uniqueRateRaw =
        rawSamples.length > 0 ? uniqueStrings.size / rawSamples.length : 0;
      const uniqueRate = Math.round(uniqueRateRaw * 10000) / 10000;

      return makeFieldInfo({
        name: col.column_name,
        dtype: duckdbTypeToInfermap(col.data_type),
        sampleValues: rawSamples.slice(0, sampleSize).map((v) => String(v)),
        nullRate,
        uniqueRate,
        valueCount: total - Math.trunc(nullRate * total),
        metadata: { db_type: col.data_type },
      });
    });

    return makeSchemaInfo({
      fields,
      sourceName: `${dbPath}:${table}`,
    });
  } finally {
    if (conn.closeSync) conn.closeSync();
    if (instance.closeSync) instance.closeSync();
  }
}
