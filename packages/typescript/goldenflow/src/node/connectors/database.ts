/**
 * Database connector for GoldenFlow (Node-only).
 *
 * Port of goldenflow/connectors/database.py. Reads/writes a Postgres table as
 * Row[]. The Python sibling uses connectorx (multi-engine); the TS port
 * targets Postgres via the `pg` driver, which is the closest broadly-used
 * Node analogue.
 *
 * `pg` is an OPTIONAL peer dependency. If it is not installed, every entry
 * point throws a clear, actionable Error (fail-soft, mirroring the Python
 * `ImportError`).
 */

import type { Row } from "../../core/types.js";

const DB_INSTALL_HINT = "Database support requires: npm install pg";

interface PgClientLike {
  connect(): Promise<void>;
  query(text: string, values?: unknown[]): Promise<{ rows: Row[] }>;
  end(): Promise<void>;
}

interface PgModule {
  Client: new (connectionString: string) => PgClientLike;
}

async function loadPg(): Promise<PgModule> {
  try {
    // `as string` defeats tsup's static resolution so the optional peer dep
    // is only required at runtime.
    const mod = (await import("pg" as string)) as PgModule | { default: PgModule };
    // `pg` ships as CommonJS; the namespace may be under `default`.
    return "Client" in mod ? (mod as PgModule) : (mod as { default: PgModule }).default;
  } catch {
    throw new Error(DB_INSTALL_HINT);
  }
}

/**
 * Quote a SQL identifier (table / column name) for Postgres. Rejects names
 * containing a double-quote to avoid identifier-injection.
 */
function quoteIdent(name: string): string {
  if (name.includes('"')) {
    throw new Error(`Invalid identifier: ${name}`);
  }
  return `"${name}"`;
}

/** Read all rows of a database table into Row[]. */
export async function readTable(connectionString: string, table: string): Promise<Row[]> {
  const { Client } = await loadPg();
  const client = new Client(connectionString);
  await client.connect();
  try {
    const result = await client.query(`SELECT * FROM ${quoteIdent(table)}`);
    return result.rows;
  } finally {
    await client.end();
  }
}

/**
 * Write Row[] to a database table. Mirrors the Python `if_table_exists="replace"`
 * semantics: drops and recreates the table, then inserts all rows.
 */
export async function writeTable(
  rows: readonly Row[],
  connectionString: string,
  table: string,
): Promise<void> {
  const { Client } = await loadPg();
  const client = new Client(connectionString);
  await client.connect();
  try {
    const ident = quoteIdent(table);
    await client.query(`DROP TABLE IF EXISTS ${ident}`);
    if (rows.length === 0) {
      await client.query(`CREATE TABLE ${ident} ()`);
      return;
    }
    const columns = Object.keys(rows[0]!);
    const colDefs = columns.map((c) => `${quoteIdent(c)} TEXT`).join(", ");
    await client.query(`CREATE TABLE ${ident} (${colDefs})`);

    const colList = columns.map(quoteIdent).join(", ");
    for (const row of rows) {
      const placeholders = columns.map((_, i) => `$${i + 1}`).join(", ");
      const values = columns.map((c) => {
        const v = row[c];
        return v === null || v === undefined ? null : String(v);
      });
      await client.query(
        `INSERT INTO ${ident} (${colList}) VALUES (${placeholders})`,
        values,
      );
    }
  } finally {
    await client.end();
  }
}
