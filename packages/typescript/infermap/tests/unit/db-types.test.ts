import { describe, it, expect } from "vitest";
import {
  sqliteTypeToInfermap,
  pgTypeToInfermap,
  duckdbTypeToInfermap,
  parseConnection,
  DbError,
} from "../../src/node/db/types.js";

describe("sqliteTypeToInfermap", () => {
  it("maps integer variants", () => {
    expect(sqliteTypeToInfermap("INTEGER")).toBe("integer");
    expect(sqliteTypeToInfermap("int")).toBe("integer");
    expect(sqliteTypeToInfermap("BIGINT")).toBe("integer");
  });
  it("maps floats, booleans, dates, datetimes", () => {
    expect(sqliteTypeToInfermap("REAL")).toBe("float");
    expect(sqliteTypeToInfermap("NUMERIC(10,2)")).toBe("float");
    expect(sqliteTypeToInfermap("BOOLEAN")).toBe("boolean");
    expect(sqliteTypeToInfermap("DATE")).toBe("date");
    expect(sqliteTypeToInfermap("DATETIME")).toBe("datetime");
    expect(sqliteTypeToInfermap("TIMESTAMP")).toBe("datetime");
  });
  it("falls back to string", () => {
    expect(sqliteTypeToInfermap("TEXT")).toBe("string");
    expect(sqliteTypeToInfermap(null)).toBe("string");
    expect(sqliteTypeToInfermap(undefined)).toBe("string");
  });
});

describe("pgTypeToInfermap", () => {
  it("maps common pg types", () => {
    expect(pgTypeToInfermap("integer")).toBe("integer");
    expect(pgTypeToInfermap("bigint")).toBe("integer");
    expect(pgTypeToInfermap("double precision")).toBe("float");
    expect(pgTypeToInfermap("numeric")).toBe("float");
    expect(pgTypeToInfermap("boolean")).toBe("boolean");
    expect(pgTypeToInfermap("date")).toBe("date");
    expect(pgTypeToInfermap("timestamp with time zone")).toBe("datetime");
    expect(pgTypeToInfermap("text")).toBe("string");
    expect(pgTypeToInfermap("character varying")).toBe("string");
  });
});

describe("duckdbTypeToInfermap", () => {
  it("maps common duckdb types", () => {
    expect(duckdbTypeToInfermap("INTEGER")).toBe("integer");
    expect(duckdbTypeToInfermap("BIGINT")).toBe("integer");
    expect(duckdbTypeToInfermap("DOUBLE")).toBe("float");
    expect(duckdbTypeToInfermap("DECIMAL")).toBe("float");
    expect(duckdbTypeToInfermap("BOOLEAN")).toBe("boolean");
    expect(duckdbTypeToInfermap("DATE")).toBe("date");
    expect(duckdbTypeToInfermap("TIMESTAMPTZ")).toBe("datetime");
    expect(duckdbTypeToInfermap("VARCHAR")).toBe("string");
  });
});

describe("parseConnection", () => {
  it("parses sqlite URIs", () => {
    const c = parseConnection("sqlite:///tmp/test.db");
    expect(c.driver).toBe("sqlite");
    if (c.driver === "sqlite") expect(c.path).toBe("tmp/test.db");
  });

  it("parses postgres URIs with full credentials", () => {
    const c = parseConnection("postgresql://alice:secret@db.example.com:5433/mydb");
    expect(c.driver).toBe("postgresql");
    if (c.driver === "postgresql") {
      expect(c.host).toBe("db.example.com");
      expect(c.port).toBe(5433);
      expect(c.user).toBe("alice");
      expect(c.password).toBe("secret");
      expect(c.database).toBe("mydb");
    }
  });

  it("defaults postgres port to 5432", () => {
    const c = parseConnection("postgres://host/db");
    if (c.driver === "postgresql") expect(c.port).toBe(5432);
  });

  it("parses mysql URIs and defaults port to 3306", () => {
    const c = parseConnection("mysql://host/db");
    expect(c.driver).toBe("mysql");
    if (c.driver === "mysql") expect(c.port).toBe(3306);
  });

  it("parses duckdb URIs", () => {
    const c = parseConnection("duckdb:///data/warehouse.duckdb");
    expect(c.driver).toBe("duckdb");
    if (c.driver === "duckdb") expect(c.path).toBe("data/warehouse.duckdb");
  });

  it("throws DbError on unknown scheme", () => {
    expect(() => parseConnection("mongodb://host/db")).toThrow(DbError);
  });

  it("throws DbError on invalid URI", () => {
    expect(() => parseConnection("not a url")).toThrow(DbError);
  });
});
