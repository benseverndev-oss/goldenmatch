/**
 * MCP `certify_semantic_model` tool dispatch: reads a model file + frame files
 * off disk, certifies each declared join key, returns the Python-parity shape.
 * The certification logic itself is parity-locked in
 * tests/parity/semantic-certify.test.ts; this covers the node wiring.
 */
import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join, relative } from "node:path";

import { handleTool } from "../../src/node/mcp/server.js";

let tmpDir: string | undefined;

function writeUnder(name: string, content: string): string {
  tmpDir ??= mkdtempSync(join(process.cwd(), "cc-certsem-"));
  const abs = join(tmpDir, name);
  writeFileSync(abs, content, "utf-8");
  return relative(process.cwd(), abs); // sanitizePath jails to cwd
}

afterEach(() => {
  if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
  tmpDir = undefined;
});

const MODEL = JSON.stringify({
  semantic_models: [
    {
      name: "orders",
      entities: [
        { name: "orders", type: "primary", expr: "resolved_entity_id" },
        { name: "customer_id", type: "unique", expr: "customer_id" },
      ],
      measures: [{ name: "revenue", agg: "sum", expr: "revenue" }],
    },
  ],
});

describe("certify_semantic_model MCP tool", () => {
  it("certifies a unique MetricFlow key as trustworthy", async () => {
    const modelPath = writeUnder("model.json", MODEL);
    const framePath = writeUnder(
      "orders.csv",
      "resolved_entity_id,customer_id,revenue\ne1,1,10\ne2,2,20\ne3,3,30\n",
    );
    const r = (await handleTool("certify_semantic_model", {
      model_path: modelPath,
      frames: { orders: framePath },
    })) as Record<string, unknown>;
    expect(r["dialect"]).toBe("metricflow");
    expect(r["n_certified"]).toBe(1);
    expect(r["all_trustworthy"]).toBe(true);
    const keys = r["keys"] as Array<Record<string, unknown>>;
    expect(keys[0]!["target"]).toBe("orders");
    expect(keys[0]!["key"]).toEqual(["resolved_entity_id"]);
    expect(keys[0]!["is_unique_at_grain"]).toBe(true);
    expect(keys[0]!["max_fan_out"]).toBe(1);
  });

  it("flags a duplicated key as untrustworthy with fan-out", async () => {
    const modelPath = writeUnder("model.json", MODEL);
    const framePath = writeUnder(
      "orders.csv",
      "resolved_entity_id,customer_id,revenue\ne1,1,10\ne1,2,30\ne2,3,5\n",
    );
    const r = (await handleTool("certify_semantic_model", {
      model_path: modelPath,
      frames: { orders: framePath },
    })) as Record<string, unknown>;
    expect(r["all_trustworthy"]).toBe(false);
    const keys = r["keys"] as Array<Record<string, unknown>>;
    expect(keys[0]!["is_unique_at_grain"]).toBe(false);
    expect(keys[0]!["max_fan_out"]).toBe(2);
    expect(keys[0]!["estimate"]).toBe(0.5);
  });

  it("resolve=true measures fragmentation / undercount", async () => {
    const modelPath = writeUnder("model.json", MODEL);
    // resolved_entity_id is unique at grain (structural pass is clean), but the
    // two duplicate-attribute rows are the same real customer under distinct
    // customer_id-derived keys... here the declared key is resolved_entity_id and
    // rows e1/e2 share identical attributes -> ER collapses them (fragmentation).
    const framePath = writeUnder(
      "orders.csv",
      "resolved_entity_id,revenue,name,email\n" +
        "e1,10,Alice Smith,alice@x.com\n" +
        "e2,20,Alice Smith,alice@x.com\n" +
        "e3,30,Bob Jones,bob@y.com\n" +
        "e4,40,Carol White,carol@z.com\n",
    );
    const r = (await handleTool("certify_semantic_model", {
      model_path: modelPath,
      frames: { orders: framePath },
      resolve: true,
    })) as Record<string, unknown>;
    expect(r["n_certified"]).toBe(1);
    const keys = r["keys"] as Array<Record<string, unknown>>;
    const k = keys[0]!;
    expect(k["is_unique_at_grain"]).toBe(true); // structural pass sees a clean key
    expect(k["resolved_entities"]).not.toBeNull();
    expect(k["fragmented_entities"] as number).toBeGreaterThanOrEqual(1);
    expect(k["undercount_estimate"] as number).toBeGreaterThan(0);
    expect(k["safe_bound"] as number).toBeLessThan(k["estimate"] as number);
  }, 20000);

  it("resolve defaults off: resolution fields are null", async () => {
    const modelPath = writeUnder("model.json", MODEL);
    const framePath = writeUnder(
      "orders.csv",
      "resolved_entity_id,customer_id,revenue\ne1,1,10\ne2,2,20\ne3,3,30\n",
    );
    const r = (await handleTool("certify_semantic_model", {
      model_path: modelPath,
      frames: { orders: framePath },
    })) as Record<string, unknown>;
    const keys = r["keys"] as Array<Record<string, unknown>>;
    expect(keys[0]!["resolved_entities"]).toBeNull();
    expect(keys[0]!["undercount_estimate"]).toBeNull();
  });

  it("errors clearly on missing model_path / frames", async () => {
    const r1 = (await handleTool("certify_semantic_model", { frames: {} })) as Record<string, unknown>;
    expect(String(r1["error"])).toMatch(/model_path is required/);
    const r2 = (await handleTool("certify_semantic_model", {
      model_path: writeUnder("m.json", MODEL),
      frames: "nope",
    })) as Record<string, unknown>;
    expect(String(r2["error"])).toMatch(/frames must map/);
  });
});
