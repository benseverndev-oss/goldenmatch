import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractSchemaFromFile } from "../../src/node/fs.js";

describe("extractSchemaFromFile (Node-only)", () => {
  let dir: string;

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), "infermap-js-"));
  });
  afterAll(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  it("reads a CSV and infers schema", async () => {
    const p = join(dir, "sales.csv");
    await writeFile(p, "order_id,amount,created_at\n1,9.99,2024-01-01\n2,19.95,2024-02-01\n");
    const schema = await extractSchemaFromFile(p);
    expect(schema.sourceName).toBe("sales");
    expect(schema.fields.map((f) => f.name)).toEqual([
      "order_id",
      "amount",
      "created_at",
    ]);
    expect(schema.fields[0]!.dtype).toBe("integer");
    expect(schema.fields[1]!.dtype).toBe("float");
    expect(schema.fields[2]!.dtype).toBe("date");
  });

  it("reads a JSON array of records", async () => {
    const p = join(dir, "users.json");
    await writeFile(p, JSON.stringify([{ id: 1, name: "alice" }, { id: 2, name: "bob" }]));
    const schema = await extractSchemaFromFile(p);
    expect(schema.fields.map((f) => f.name)).toEqual(["id", "name"]);
  });

  it("reads a JSON schema definition", async () => {
    const p = join(dir, "user.schema.json");
    await writeFile(
      p,
      JSON.stringify({
        fields: [
          { name: "id", required: true },
          { name: "email", aliases: ["email_addr"] },
        ],
      })
    );
    const schema = await extractSchemaFromFile(p);
    expect(schema.requiredFields).toEqual(["id"]);
    expect(schema.fields[1]!.metadata["aliases"]).toEqual(["email_addr"]);
  });

  it("throws on unsupported extension", async () => {
    const p = join(dir, "file.xyz");
    await writeFile(p, "nope");
    await expect(extractSchemaFromFile(p)).rejects.toThrow();
  });
});
