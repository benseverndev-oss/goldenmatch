/**
 * Semantic catalog emitters — the store-emit surface + the PyYAML-safe_dump
 * serializer (`yamlEmit.ts`). Byte parity with the Python emitters is locked by
 * tests/parity/semantic-catalog-emit.test.ts; this file covers the live
 * store-path (`emitSemanticModelFromStore`) and the serializer's scalar-quoting
 * / float-formatting rules directly.
 */
import { describe, it, expect } from "vitest";

import { InMemoryIdentityStore } from "../../src/core/identity/in-memory-store.js";
import { emitSemanticModelFromStore } from "../../src/core/semantic/catalog.js";
import { ResolvedCrosswalk } from "../../src/core/semantic/crosswalk.js";
import { dumpYaml, pyFloat, PyFloat } from "../../src/core/semantic/yamlEmit.js";

const NOW = new Date("2026-01-01T00:00:00.000Z");

async function seedStore(): Promise<InMemoryIdentityStore> {
  const store = new InMemoryIdentityStore();
  for (const [entityId, recordId] of [
    ["E1", "crm:1"],
    ["E1", "crm:2"],
    ["E2", "crm:3"],
  ] as const) {
    if (!(await store.getIdentity(entityId))) {
      await store.upsertIdentity({
        entityId,
        status: "active",
        mergedInto: null,
        goldenRecord: null,
        confidence: 0.9,
        dataset: "crm",
        createdAt: NOW,
        updatedAt: NOW,
      });
    }
    await store.upsertRecord({
      recordId,
      source: "crm",
      sourcePk: recordId.split(":")[1]!,
      recordHash: recordId,
      entityId,
      payload: null,
      dataset: "crm",
      firstSeenAt: NOW,
      lastSeenAt: NOW,
    });
  }
  return store;
}

describe("emitSemanticModelFromStore", () => {
  it("metricflow: reads live stats (3 records, 2 entities) off the store", async () => {
    const store = await seedStore();
    const yaml = await emitSemanticModelFromStore(store, {
      sourceName: "customers",
      sourcePkColumn: "customer_id",
      dataset: "crm",
    });
    // Primary entity declares the conformed resolved key; the source PK is unique.
    expect(yaml).toContain("expr: resolved_entity_id");
    expect(yaml).toContain("- name: customer_id\n    type: unique\n    expr: customer_id");
    expect(yaml).toBe(
      [
        "semantic_models:",
        "- name: customers",
        "  model: ref('customers')",
        "  entities:",
        "  - name: customers",
        "    type: primary",
        "    expr: resolved_entity_id",
        "  - name: customer_id",
        "    type: unique",
        "    expr: customer_id",
        "",
      ].join("\n"),
    );
  });

  it("cube: provenance stats + reduction_ratio ride in meta.goldenmatch", async () => {
    const store = await seedStore();
    const yaml = await emitSemanticModelFromStore(store, {
      sourceName: "customers",
      sourcePkColumn: "customer_id",
      dialect: "cube",
      dataset: "crm",
    });
    expect(yaml).toContain("n_records: 3");
    expect(yaml).toContain("n_entities: 2");
    // 1 - 2/3 rounded to 6dp.
    expect(yaml).toContain("reduction_ratio: 0.333333");
    expect(yaml).toContain("sql: '{CUBE}.customer_id = {crosswalk.customer_id}'");
  });

  it("osi: emits the versioned document + custom_extensions provenance", async () => {
    const store = await seedStore();
    const yaml = await emitSemanticModelFromStore(store, {
      sourceName: "customers",
      sourcePkColumn: "customer_id",
      dialect: "osi",
      dataset: "crm",
    });
    expect(yaml.startsWith("version: 0.2.0.dev0\n")).toBe(true);
    expect(yaml).toContain("name: customers_resolved");
    expect(yaml).toContain("dialect: ANSI_SQL");
    expect(yaml).toContain("n_records: 3");
  });

  it("resolved_key override flows into the primary entity expr", async () => {
    const store = await seedStore();
    const yaml = await emitSemanticModelFromStore(store, {
      sourceName: "customers",
      sourcePkColumn: "customer_id",
      resolvedKey: "gm_entity_id",
      dataset: "crm",
    });
    expect(yaml).toContain("expr: gm_entity_id");
  });

  it("unknown dialect throws", async () => {
    const store = await seedStore();
    await expect(
      emitSemanticModelFromStore(store, {
        sourceName: "c",
        sourcePkColumn: "id",
        dialect: "looker" as unknown as "metricflow",
      }),
    ).rejects.toThrow(/unknown dialect/);
  });
});

describe("ResolvedCrosswalk.reductionRatio", () => {
  it("0 records -> 0.0 (guarded)", () => {
    const xw = new ResolvedCrosswalk({ source: "s", sourcePkColumn: "id" });
    expect(xw.reductionRatio).toBe(0.0);
  });
  it("80/100 -> 0.2", () => {
    const xw = new ResolvedCrosswalk({
      source: "s",
      sourcePkColumn: "id",
      nRecords: 100,
      nEntities: 80,
    });
    expect(xw.reductionRatio).toBeCloseTo(0.2, 12);
  });
});

describe("dumpYaml scalar quoting (PyYAML safe_dump parity)", () => {
  it("plain identifiers stay unquoted", () => {
    expect(dumpYaml({ a: "customer_id" })).toBe("a: customer_id\n");
  });
  it("bool-like / null-like / numeric strings are single-quoted", () => {
    expect(dumpYaml({ a: "yes" })).toBe("a: 'yes'\n");
    expect(dumpYaml({ a: "no" })).toBe("a: 'no'\n");
    expect(dumpYaml({ a: "on" })).toBe("a: 'on'\n");
    expect(dumpYaml({ a: "true" })).toBe("a: 'true'\n");
    expect(dumpYaml({ a: "null" })).toBe("a: 'null'\n");
    expect(dumpYaml({ a: "123" })).toBe("a: '123'\n");
    expect(dumpYaml({ a: "1.5" })).toBe("a: '1.5'\n");
    expect(dumpYaml({ a: "" })).toBe("a: ''\n");
  });
  it("ref('x') stays plain but {CUBE}... SQL is single-quoted", () => {
    expect(dumpYaml({ a: "ref('customers')" })).toBe("a: ref('customers')\n");
    expect(dumpYaml({ a: "{CUBE}.x = {c.y}" })).toBe("a: '{CUBE}.x = {c.y}'\n");
  });
  it("leading/trailing whitespace and ': ' / ' #' force quoting", () => {
    expect(dumpYaml({ a: " x" })).toBe("a: ' x'\n");
    expect(dumpYaml({ a: "x " })).toBe("a: 'x '\n");
    expect(dumpYaml({ a: "k: v" })).toBe("a: 'k: v'\n");
    expect(dumpYaml({ a: "x #c" })).toBe("a: 'x #c'\n");
  });
  it("internal single quotes are doubled", () => {
    expect(dumpYaml({ a: "it's" })).toBe("a: it's\n"); // no leading indicator -> plain
    expect(dumpYaml({ a: "'lead" })).toBe("a: '''lead'\n");
  });
  it("true booleans / ints / null render bare; PyFloat keeps its decimal", () => {
    expect(dumpYaml({ a: true, b: false, c: null, d: 42 })).toBe(
      "a: true\nb: false\nc: null\nd: 42\n",
    );
    expect(dumpYaml({ r: pyFloat(0) })).toBe("r: 0.0\n");
    expect(dumpYaml({ r: pyFloat(0.2) })).toBe("r: 0.2\n");
    expect(pyFloat(1) instanceof PyFloat).toBe(true);
  });
  it("empty map / list render inline", () => {
    expect(dumpYaml({ a: {}, b: [] })).toBe("a: {}\nb: []\n");
  });
});
