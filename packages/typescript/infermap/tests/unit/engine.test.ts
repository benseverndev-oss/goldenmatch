import { describe, it, expect } from "vitest";
import { MapEngine } from "../../src/core/engine.js";
import {
  makeFieldInfo,
  makeSchemaInfo,
  type SchemaInfo,
} from "../../src/core/types.js";
import { ExactScorer } from "../../src/core/scorers/exact.js";
import { AliasScorer } from "../../src/core/scorers/alias.js";
import { FuzzyNameScorer } from "../../src/core/scorers/fuzzy-name.js";
import { defineScorer } from "../../src/core/scorers/registry.js";
import { makeScorerResult } from "../../src/core/types.js";

const schema = (names: string[], extras: Record<string, unknown> = {}): SchemaInfo =>
  makeSchemaInfo({
    fields: names.map((n) => makeFieldInfo({ name: n, ...extras })),
  });

describe("MapEngine.mapSchemas", () => {
  it("maps exact-name fields 1:1", () => {
    const engine = new MapEngine();
    const src = schema(["first_name", "last_name", "email"]);
    const tgt = schema(["first_name", "last_name", "email"]);
    const result = engine.mapSchemas(src, tgt);

    expect(result.mappings).toHaveLength(3);
    const pairs = result.mappings.map((m) => [m.source, m.target]);
    expect(pairs).toContainEqual(["first_name", "first_name"]);
    expect(pairs).toContainEqual(["last_name", "last_name"]);
    expect(pairs).toContainEqual(["email", "email"]);
    expect(result.unmappedSource).toEqual([]);
    expect(result.unmappedTarget).toEqual([]);
  });

  it("maps alias fields to canonical", () => {
    const engine = new MapEngine();
    const src = schema(["fname", "lname", "email_addr"]);
    const tgt = schema(["first_name", "last_name", "email"]);
    const result = engine.mapSchemas(src, tgt);

    const pairs = new Map(result.mappings.map((m) => [m.source, m.target]));
    expect(pairs.get("fname")).toBe("first_name");
    expect(pairs.get("lname")).toBe("last_name");
    expect(pairs.get("email_addr")).toBe("email");
  });

  it("leaves unknown fields unmapped below minConfidence", () => {
    const engine = new MapEngine({ minConfidence: 0.5 });
    const src = schema(["customer_id"]);
    const tgt = schema(["random_xyz"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.mappings).toHaveLength(0);
    expect(result.unmappedSource).toEqual(["customer_id"]);
    expect(result.unmappedTarget).toEqual(["random_xyz"]);
  });

  it("warns on unmapped required target fields", () => {
    const engine = new MapEngine({ minConfidence: 0.9 });
    const src = schema(["aaa"]);
    const tgt = makeSchemaInfo({
      fields: [
        makeFieldInfo({ name: "first_name" }),
        makeFieldInfo({ name: "email" }),
      ],
      requiredFields: ["email"],
    });
    const result = engine.mapSchemas(src, tgt);
    expect(result.warnings.some((w) => w.includes("email"))).toBe(true);
  });

  it("excludes single-contributor scores (MIN_CONTRIBUTORS)", () => {
    // Only ExactScorer is configured — one scorer can't satisfy the
    // 2-contributor minimum, so all scores should collapse to 0.
    const engine = new MapEngine({ scorers: [new ExactScorer()] });
    const src = schema(["email"]);
    const tgt = schema(["email"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.mappings).toHaveLength(0);
  });

  it("accepts custom scorers via defineScorer", () => {
    const always1 = defineScorer(
      "Always1",
      () => makeScorerResult(1, "always"),
      1
    );
    const engine = new MapEngine({
      scorers: [new ExactScorer(), always1],
      minConfidence: 0.5,
    });
    const src = schema(["aaa"]);
    const tgt = schema(["bbb"]);
    const result = engine.mapSchemas(src, tgt);
    // Exact=0, Always1=1, avg = 0.5 → passes min_confidence
    expect(result.mappings).toHaveLength(1);
  });

  it("isolates scorer exceptions and still produces a result", () => {
    const broken = defineScorer(
      "Broken",
      () => {
        throw new Error("boom");
      },
      1
    );
    const errors: string[] = [];
    const engine = new MapEngine({
      scorers: [new ExactScorer(), new AliasScorer(), new FuzzyNameScorer(), broken],
      onScorerError: ({ scorer }) => errors.push(scorer),
    });
    const src = schema(["email"]);
    const tgt = schema(["email"]);
    const result = engine.mapSchemas(src, tgt);
    expect(errors).toContain("Broken");
    expect(result.mappings).toHaveLength(1);
  });

  it("merges schemaFile aliases into target metadata", () => {
    const engine = new MapEngine();
    const src = schema(["cust_no"]);
    const tgt = schema(["customer_id"]);
    const schemaFile = makeSchemaInfo({
      fields: [
        makeFieldInfo({
          name: "customer_id",
          metadata: { aliases: ["cust_no", "customer_number"] },
        }),
      ],
    });
    const result = engine.mapSchemas(src, tgt, { schemaFile });
    expect(result.mappings).toHaveLength(1);
    expect(result.mappings[0]!.target).toBe("customer_id");
  });

  it("populates metadata with timing and counts", () => {
    const engine = new MapEngine();
    const src = schema(["email"]);
    const tgt = schema(["email"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.metadata["source_field_count"]).toBe(1);
    expect(result.metadata["target_field_count"]).toBe(1);
    expect(result.metadata["mapping_count"]).toBe(1);
    expect(result.metadata["min_confidence"]).toBe(0.3);
    expect(typeof result.metadata["elapsed_seconds"]).toBe("number");
  });

  it("omits scoreMatrix by default", () => {
    const engine = new MapEngine();
    const src = schema(["fname", "lname"]);
    const tgt = schema(["first_name", "last_name"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.scoreMatrix).toBeUndefined();
  });

  it("populates scoreMatrix when returnScoreMatrix flag is set", () => {
    const engine = new MapEngine({ returnScoreMatrix: true });
    const src = schema(["fname", "lname"]);
    const tgt = schema(["first_name", "last_name"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.scoreMatrix).toBeDefined();
    expect(Object.keys(result.scoreMatrix!)).toEqual(["fname", "lname"]);
    for (const row of Object.values(result.scoreMatrix!)) {
      expect(Object.keys(row)).toEqual(["first_name", "last_name"]);
      for (const score of Object.values(row)) {
        expect(score).toBeGreaterThanOrEqual(0);
        expect(score).toBeLessThanOrEqual(1);
      }
    }
  });

  it("scoreMatrix scores match assigned mapping confidences", () => {
    const engine = new MapEngine({ returnScoreMatrix: true, minConfidence: 0 });
    const src = schema(["fname", "email_addr"]);
    const tgt = schema(["first_name", "email"]);
    const result = engine.mapSchemas(src, tgt);
    expect(result.scoreMatrix).toBeDefined();
    for (const m of result.mappings) {
      const smScore = result.scoreMatrix![m.source]![m.target]!;
      expect(Math.abs(smScore - m.confidence)).toBeLessThan(0.01);
    }
  });
});
