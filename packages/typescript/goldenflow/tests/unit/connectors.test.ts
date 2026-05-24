/**
 * Tests for the cloud connectors (s3, gcs, database).
 *
 * Fail-soft behavior mirrors packages/python/goldenflow/tests/connectors/.
 * Happy paths mock the optional peer SDKs so the suite runs without installing
 * @aws-sdk/client-s3, @google-cloud/storage, or pg.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  parseS3Uri,
  parseGcsUri,
} from "../../src/node/connectors/index.js";
import type { Row } from "../../src/core/types.js";

// ---------------------------------------------------------------------------
// URI parsing (no SDK needed)
// ---------------------------------------------------------------------------

describe("S3 URI parsing", () => {
  it("parses s3://bucket/key", () => {
    expect(parseS3Uri("s3://my-bucket/path/to/file.csv")).toEqual([
      "my-bucket",
      "path/to/file.csv",
    ]);
  });
  it("rejects non-s3 URIs", () => {
    expect(() => parseS3Uri("https://x/y")).toThrow(/Must start with s3:\/\//);
  });
  it("rejects bucket-only URIs", () => {
    expect(() => parseS3Uri("s3://bucket")).toThrow(/Must be s3:\/\/bucket\/key/);
  });
});

describe("GCS URI parsing", () => {
  it("parses gs://bucket/path", () => {
    expect(parseGcsUri("gs://my-bucket/path/to/file.csv")).toEqual([
      "my-bucket",
      "path/to/file.csv",
    ]);
  });
  it("rejects non-gs URIs", () => {
    expect(() => parseGcsUri("s3://x/y")).toThrow(/Must start with gs:\/\//);
  });
});

// ---------------------------------------------------------------------------
// Fail-soft: optional SDK not installed -> clear Error
// ---------------------------------------------------------------------------

describe("connectors fail-soft when SDK missing", () => {
  // No vi.mock here, so the real (uninstalled) modules fail to import.
  it("readS3 throws install hint", async () => {
    const { readS3 } = await import("../../src/node/connectors/s3.js");
    await expect(readS3("s3://bucket/file.csv")).rejects.toThrow(
      "S3 support requires: npm install @aws-sdk/client-s3",
    );
  });

  it("writeS3 throws install hint", async () => {
    const { writeS3 } = await import("../../src/node/connectors/s3.js");
    await expect(writeS3([], "s3://bucket/file.csv")).rejects.toThrow(
      "S3 support requires: npm install @aws-sdk/client-s3",
    );
  });

  it("readGcs throws install hint", async () => {
    const { readGcs } = await import("../../src/node/connectors/gcs.js");
    await expect(readGcs("gs://bucket/file.csv")).rejects.toThrow(
      "GCS support requires: npm install @google-cloud/storage",
    );
  });

  it("writeGcs throws install hint", async () => {
    const { writeGcs } = await import("../../src/node/connectors/gcs.js");
    await expect(writeGcs([], "gs://bucket/file.csv")).rejects.toThrow(
      "GCS support requires: npm install @google-cloud/storage",
    );
  });

  it("readTable throws install hint", async () => {
    const { readTable } = await import("../../src/node/connectors/database.js");
    await expect(readTable("postgresql://localhost/test", "users")).rejects.toThrow(
      "Database support requires: npm install pg",
    );
  });

  it("writeTable throws install hint", async () => {
    const { writeTable } = await import("../../src/node/connectors/database.js");
    await expect(
      writeTable([{ a: 1 }], "postgresql://localhost/test", "users"),
    ).rejects.toThrow("Database support requires: npm install pg");
  });
});

// ---------------------------------------------------------------------------
// Happy paths with mocked SDKs
// ---------------------------------------------------------------------------

describe("S3 happy path (mocked SDK)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("readS3 downloads CSV and parses to Row[]", async () => {
    const captured: { command?: unknown } = {};
    vi.doMock("@aws-sdk/client-s3", () => ({
      S3Client: class {
        async send(command: unknown) {
          captured.command = command;
          return { Body: "id,name\n1,Alice\n2,Bob\n" };
        }
      },
      GetObjectCommand: class {
        constructor(public input: unknown) {}
      },
      PutObjectCommand: class {
        constructor(public input: unknown) {}
      },
    }));

    const { readS3 } = await import("../../src/node/connectors/s3.js");
    const rows = await readS3("s3://bucket/data.csv");
    expect(rows.length).toBe(2);
    expect(rows[0]).toMatchObject({ name: "Alice" });
    expect(rows[1]).toMatchObject({ name: "Bob" });
    vi.doUnmock("@aws-sdk/client-s3");
  });

  it("writeS3 uploads serialized CSV", async () => {
    let putBody: string | undefined;
    vi.doMock("@aws-sdk/client-s3", () => ({
      S3Client: class {
        async send(command: { input?: { Body?: string } }) {
          if (command?.input?.Body !== undefined) putBody = command.input.Body;
          return {};
        }
      },
      GetObjectCommand: class {
        constructor(public input: unknown) {}
      },
      PutObjectCommand: class {
        constructor(public input: { Body?: string }) {}
      },
    }));

    const { writeS3 } = await import("../../src/node/connectors/s3.js");
    const rows: Row[] = [{ id: 1, name: "Alice" }];
    await writeS3(rows, "s3://bucket/out.csv");
    expect(putBody).toContain("id,name");
    expect(putBody).toContain("Alice");
    vi.doUnmock("@aws-sdk/client-s3");
  });
});

describe("GCS happy path (mocked SDK)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("readGcs downloads CSV and parses to Row[]", async () => {
    vi.doMock("@google-cloud/storage", () => ({
      Storage: class {
        bucket() {
          return {
            file() {
              return {
                async download() {
                  return [Buffer.from("id,name\n1,Alice\n")];
                },
                async save() {},
              };
            },
          };
        }
      },
    }));

    const { readGcs } = await import("../../src/node/connectors/gcs.js");
    const rows = await readGcs("gs://bucket/data.csv");
    expect(rows.length).toBe(1);
    expect(rows[0]).toMatchObject({ name: "Alice" });
    vi.doUnmock("@google-cloud/storage");
  });

  it("writeGcs saves serialized CSV", async () => {
    let saved: string | undefined;
    vi.doMock("@google-cloud/storage", () => ({
      Storage: class {
        bucket() {
          return {
            file() {
              return {
                async download() {
                  return [Buffer.from("")];
                },
                async save(data: string | Buffer) {
                  saved = typeof data === "string" ? data : data.toString("utf-8");
                },
              };
            },
          };
        }
      },
    }));

    const { writeGcs } = await import("../../src/node/connectors/gcs.js");
    await writeGcs([{ id: 1, name: "Alice" }], "gs://bucket/out.csv");
    expect(saved).toContain("id,name");
    expect(saved).toContain("Alice");
    vi.doUnmock("@google-cloud/storage");
  });
});

describe("Database happy path (mocked pg)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("readTable runs SELECT and returns rows", async () => {
    let queryText: string | undefined;
    vi.doMock("pg", () => ({
      Client: class {
        async connect() {}
        async query(text: string) {
          queryText = text;
          return { rows: [{ id: 1, name: "Alice" }] };
        }
        async end() {}
      },
    }));

    const { readTable } = await import("../../src/node/connectors/database.js");
    const rows = await readTable("postgresql://localhost/test", "users");
    expect(queryText).toBe('SELECT * FROM "users"');
    expect(rows).toEqual([{ id: 1, name: "Alice" }]);
    vi.doUnmock("pg");
  });

  it("writeTable drops, creates, and inserts", async () => {
    const queries: string[] = [];
    vi.doMock("pg", () => ({
      Client: class {
        async connect() {}
        async query(text: string) {
          queries.push(text);
          return { rows: [] };
        }
        async end() {}
      },
    }));

    const { writeTable } = await import("../../src/node/connectors/database.js");
    await writeTable([{ id: 1, name: "Alice" }], "postgresql://localhost/test", "users");
    expect(queries.some((q) => q.startsWith("DROP TABLE IF EXISTS"))).toBe(true);
    expect(queries.some((q) => q.startsWith("CREATE TABLE"))).toBe(true);
    expect(queries.some((q) => q.startsWith("INSERT INTO"))).toBe(true);
    vi.doUnmock("pg");
  });

  it("rejects table names containing a quote", async () => {
    vi.doMock("pg", () => ({
      Client: class {
        async connect() {}
        async query() {
          return { rows: [] };
        }
        async end() {}
      },
    }));
    const { readTable } = await import("../../src/node/connectors/database.js");
    await expect(readTable("postgresql://localhost/test", 'bad"name')).rejects.toThrow(
      /Invalid identifier/,
    );
    vi.doUnmock("pg");
  });
});
