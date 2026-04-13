/**
 * Database scanner — scan tables directly from Postgres, MySQL, or SQLite.
 * Port of goldencheck/engine/db_scanner.py. Node-only.
 *
 * Database drivers are optional peer dependencies:
 * - Postgres: `pg`
 * - MySQL: `mysql2`
 * - SQLite: `better-sqlite3`
 */

import { TabularData, type Row } from "../core/data.js";
import { scanData } from "../core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../core/engine/confidence.js";
import type { ScanResult } from "../core/types.js";

export async function scanDatabase(
  connectionString: string,
  options?: {
    table?: string;
    query?: string;
    sampleSize?: number;
    domain?: string;
  },
): Promise<ScanResult> {
  const table = options?.table;
  const query = options?.query;
  const sampleSize = options?.sampleSize ?? 100_000;
  const domain = options?.domain;

  if (!table && !query) {
    throw new Error("Either 'table' or 'query' must be provided.");
  }

  const sql = query ?? `SELECT * FROM ${table} LIMIT ${sampleSize}`;

  // Determine driver from connection string and fetch rows
  const rows = await executeQuery(connectionString, sql);

  if (rows.length === 0) {
    const data = new TabularData([]);
    const result = scanData(data, { sampleSize, domain: domain ?? undefined });
    return {
      findings: applyConfidenceDowngrade(result.findings, false),
      profile: {
        ...result.profile,
        filePath: `${maskPassword(connectionString)}:${table ?? "custom query"}`,
      },
    };
  }

  const data = new TabularData(rows);
  const result = scanData(data, { sampleSize, domain: domain ?? undefined });
  const findings = applyConfidenceDowngrade(result.findings, false);

  // Replace file path with source info (masked connection string)
  const source = table ?? "custom query";
  const profile = {
    ...result.profile,
    filePath: `${maskPassword(connectionString)}:${source}`,
  };

  return { findings, profile };
}

// --- Driver detection & query execution ---

type DriverType = "postgres" | "mysql" | "sqlite";

function detectDriver(connectionString: string): DriverType {
  if (connectionString.startsWith("postgres://") || connectionString.startsWith("postgresql://")) {
    return "postgres";
  }
  if (connectionString.startsWith("mysql://")) {
    return "mysql";
  }
  if (connectionString.startsWith("sqlite://") || connectionString.startsWith("sqlite3://")) {
    return "sqlite";
  }
  throw new Error(
    `Unsupported database URL scheme. Expected postgres://, mysql://, or sqlite://. Got: ${connectionString.split("://")[0]}://`,
  );
}

async function executeQuery(connectionString: string, sql: string): Promise<Row[]> {
  const driver = detectDriver(connectionString);

  switch (driver) {
    case "postgres":
      return executePostgres(connectionString, sql);
    case "mysql":
      return executeMysql(connectionString, sql);
    case "sqlite":
      return executeSqlite(connectionString, sql);
  }
}

async function executePostgres(connectionString: string, sql: string): Promise<Row[]> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  let pg: any;
  try {
    pg = require("pg");
  } catch {
    throw new Error(
      "Postgres scanning requires the 'pg' package. Install with: npm install pg",
    );
  }

  const Client = pg.default?.Client ?? pg.Client;
  const client = new Client({ connectionString });

  try {
    await client.connect();
    const result = await client.query(sql);
    return result.rows as Row[];
  } finally {
    await client.end();
  }
}

async function executeMysql(connectionString: string, sql: string): Promise<Row[]> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  let mysql2: any;
  try {
    mysql2 = require("mysql2/promise");
  } catch {
    throw new Error(
      "MySQL scanning requires the 'mysql2' package. Install with: npm install mysql2",
    );
  }

  const connection = await mysql2.createConnection(connectionString);

  try {
    const [rows] = await connection.execute(sql);
    return rows as Row[];
  } finally {
    await connection.end();
  }
}

function executeSqlite(connectionString: string, sql: string): Promise<Row[]> {
  const dbPath = connectionString.replace(/^sqlite3?:\/\//, "");

  // eslint-disable-next-line @typescript-eslint/no-require-imports
  let Database: any;
  try {
    Database = require("better-sqlite3");
  } catch {
    throw new Error(
      "SQLite scanning requires the 'better-sqlite3' package. Install with: npm install better-sqlite3",
    );
  }

  const db = Database(dbPath);
  try {
    const rows = db.prepare(sql).all() as Row[];
    return Promise.resolve(rows);
  } finally {
    db.close();
  }
}

// --- Helpers ---

/** Mask password in connection string for display. */
function maskPassword(url: string): string {
  return url.replace(/:\/\/([^:]+):([^@]+)@/, "://$1:***@");
}
