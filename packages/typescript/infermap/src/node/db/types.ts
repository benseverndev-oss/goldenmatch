// DB type mapping + URI parsing. Pure functions, no driver deps.
// Mirrors the helpers in infermap/providers/db.py.
import type { Dtype } from "../../core/types.js";

export function sqliteTypeToInfermap(sqliteType: string | null | undefined): Dtype {
  const t = (sqliteType ?? "").toUpperCase().trim();
  if (t.includes("INT")) return "integer";
  if (/REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL/.test(t)) return "float";
  if (t.includes("BOOL")) return "boolean";
  if (t.includes("DATE") && !t.includes("TIME")) return "date";
  if (t.includes("DATETIME") || t.includes("TIMESTAMP")) return "datetime";
  return "string";
}

export function pgTypeToInfermap(pgType: string | null | undefined): Dtype {
  const t = (pgType ?? "").toLowerCase().trim();
  if (["integer", "bigint", "smallint", "serial", "bigserial"].includes(t))
    return "integer";
  if (["real", "double precision", "numeric", "decimal", "money"].includes(t))
    return "float";
  if (t === "boolean") return "boolean";
  if (t === "date") return "date";
  if (
    t === "timestamp" ||
    t === "timestamp with time zone" ||
    t === "timestamp without time zone"
  )
    return "datetime";
  return "string";
}

export function duckdbTypeToInfermap(
  duckdbType: string | null | undefined
): Dtype {
  const t = (duckdbType ?? "").toUpperCase().trim();
  if (
    [
      "INTEGER",
      "BIGINT",
      "SMALLINT",
      "TINYINT",
      "HUGEINT",
      "INT4",
      "INT8",
      "INT2",
      "INT1",
    ].includes(t)
  )
    return "integer";
  if (
    ["REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "FLOAT4", "FLOAT8"].includes(
      t
    )
  )
    return "float";
  if (t === "BOOLEAN") return "boolean";
  if (t === "DATE") return "date";
  if (
    t === "TIMESTAMP" ||
    t === "TIMESTAMP WITH TIME ZONE" ||
    t === "TIMESTAMPTZ"
  )
    return "datetime";
  return "string";
}

export type DbDriver = "sqlite" | "postgresql" | "mysql" | "duckdb";

export interface SqliteConnInfo {
  driver: "sqlite";
  path: string;
}
export interface DuckdbConnInfo {
  driver: "duckdb";
  path: string;
}
export interface PgConnInfo {
  driver: "postgresql";
  host: string | null;
  port: number;
  user: string | null;
  password: string | null;
  database: string;
}
export interface MysqlConnInfo {
  driver: "mysql";
  host: string | null;
  port: number;
  user: string | null;
  password: string | null;
  database: string;
}
export type ConnInfo = SqliteConnInfo | DuckdbConnInfo | PgConnInfo | MysqlConnInfo;

export class DbError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DbError";
  }
}

/**
 * Parse a database URI into normalized ConnInfo.
 * Supported schemes: sqlite, postgresql/postgres, mysql, duckdb.
 */
export function parseConnection(uri: string): ConnInfo {
  let parsed: URL;
  try {
    parsed = new URL(uri);
  } catch {
    throw new DbError(`Invalid database URI: ${uri}`);
  }
  const scheme = parsed.protocol.replace(/:$/, "").toLowerCase();

  if (scheme === "sqlite") {
    // sqlite:///path  → pathname starts with /
    // sqlite:///C:/path on Windows → /C:/path
    const raw = parsed.pathname;
    const path = raw.startsWith("/") ? raw.slice(1) : raw;
    return { driver: "sqlite", path: decodeURIComponent(path) };
  }

  if (scheme === "duckdb") {
    const raw = parsed.pathname;
    const path = raw.startsWith("/") ? raw.slice(1) : raw;
    return { driver: "duckdb", path: decodeURIComponent(path) };
  }

  if (scheme === "postgresql" || scheme === "postgres") {
    return {
      driver: "postgresql",
      host: parsed.hostname || null,
      port: parsed.port ? Number(parsed.port) : 5432,
      user: parsed.username ? decodeURIComponent(parsed.username) : null,
      password: parsed.password ? decodeURIComponent(parsed.password) : null,
      database: parsed.pathname.replace(/^\//, ""),
    };
  }

  if (scheme === "mysql") {
    return {
      driver: "mysql",
      host: parsed.hostname || null,
      port: parsed.port ? Number(parsed.port) : 3306,
      user: parsed.username ? decodeURIComponent(parsed.username) : null,
      password: parsed.password ? decodeURIComponent(parsed.password) : null,
      database: parsed.pathname.replace(/^\//, ""),
    };
  }

  throw new DbError(`Unsupported database scheme: ${scheme}`);
}
