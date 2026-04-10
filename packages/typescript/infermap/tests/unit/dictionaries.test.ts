// Tests for src/core/dictionaries + the domain-aware AliasScorer path.
// Mirrors tests/test_dictionaries.py.
import { describe, it, expect } from "vitest";
import {
  UnknownDomainError,
  availableDomains,
  loadDomain,
  mergeDomains,
} from "../../src/core/dictionaries/index.js";
import { AliasScorer, buildLookup } from "../../src/core/scorers/alias.js";
import { MapEngine } from "../../src/core/engine.js";
import { makeFieldInfo, makeSchemaInfo } from "../../src/core/types.js";

const f = (name: string, samples: string[] = ["a"]) =>
  makeFieldInfo({ name, dtype: "string", sampleValues: samples, valueCount: samples.length });

describe("availableDomains", () => {
  it("includes the four shipped dictionaries", () => {
    const domains = availableDomains();
    for (const name of ["generic", "healthcare", "finance", "ecommerce"]) {
      expect(domains).toContain(name);
    }
  });
});

describe("loadDomain", () => {
  it("loads generic with email aliases", () => {
    const d = loadDomain("generic");
    expect(d["email"]).toContain("e_mail");
  });

  it("loads healthcare with mrn / patient_id", () => {
    const d = loadDomain("healthcare");
    expect(d["patient_id"]).toContain("mrn");
  });

  it("throws UnknownDomainError on unknown name", () => {
    expect(() => loadDomain("not_a_real_domain")).toThrow(UnknownDomainError);
  });

  it("returns mutable copies (modifying does not affect future loads)", () => {
    const a = loadDomain("generic");
    a["email"]!.push("custom_email_alias");
    const b = loadDomain("generic");
    expect(b["email"]).not.toContain("custom_email_alias");
  });
});

describe("mergeDomains", () => {
  it("unions alias lists across domains", () => {
    const merged = mergeDomains(["generic", "ecommerce"]);
    expect(merged["email"]).toBeDefined(); // from generic
    expect(merged["product_id"]).toBeDefined(); // from ecommerce
  });

  it("does not duplicate aliases when the same domain appears twice", () => {
    const once = loadDomain("generic");
    const twice = mergeDomains(["generic", "generic"]);
    for (const k of Object.keys(once)) {
      expect(twice[k]!.length).toBe(once[k]!.length);
    }
  });
});

describe("buildLookup", () => {
  it("maps every alias to its canonical key", () => {
    const lookup = buildLookup({ email: ["e_mail", "contact_email"] });
    expect(lookup.get("email")).toBe("email");
    expect(lookup.get("e_mail")).toBe("email");
    expect(lookup.get("contact_email")).toBe("email");
  });

  it("lowercases aliases", () => {
    const lookup = buildLookup({ email: ["CONTACT_EMAIL"] });
    expect(lookup.get("contact_email")).toBe("email");
  });
});

describe("AliasScorer per-instance dict isolation", () => {
  it("a per-instance dict ignores the module-level default", () => {
    const scorer = new AliasScorer({ aliases: { foo_canonical: ["foo", "foo_alias"] } });
    const result = scorer.score(f("foo"), f("foo_alias"));
    expect(result).not.toBeNull();
    expect(result!.score).toBeCloseTo(0.95, 5);

    // Per-instance dict doesn't know "email" — should abstain.
    const r2 = scorer.score(f("email"), f("e_mail"));
    expect(r2).toBeNull();
  });

  it("default AliasScorer reads the module-level dict", () => {
    const scorer = new AliasScorer();
    const r = scorer.score(f("email"), f("e_mail"));
    expect(r).not.toBeNull();
    expect(r!.score).toBeCloseTo(0.95, 5);
  });
});

describe("MapEngine default is generic-only", () => {
  it("does NOT pick up healthcare aliases without explicit domain", () => {
    const src = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "mrn", dtype: "string", sampleValues: ["A"], valueCount: 1 })],
    });
    const tgt = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "patient_id", dtype: "string", sampleValues: ["1"], valueCount: 1 })],
    });
    const result = new MapEngine().mapSchemas(src, tgt);
    if (result.mappings.length > 0) {
      // If fuzzy/profile produces a mapping, confidence must be low (no alias bonus)
      expect(result.mappings[0]!.confidence).toBeLessThan(0.4);
    }
  });
});

describe("MapEngine + domains", () => {
  it("matches mrn -> patient_id with healthcare loaded", () => {
    const src = makeSchemaInfo({ fields: [f("mrn", ["A", "B", "C"])] });
    const tgt = makeSchemaInfo({ fields: [f("patient_id", ["1", "2", "3"])] });

    const base = new MapEngine().mapSchemas(src, tgt);
    const baseConf = base.mappings[0]?.confidence ?? 0;

    const dom = new MapEngine({ domains: ["healthcare"] }).mapSchemas(src, tgt);
    expect(dom.mappings.length).toBeGreaterThan(0);
    expect(dom.mappings[0]!.confidence).toBeGreaterThan(baseConf);
  });

  it("matches finance txn / amt / ccy abbreviations", () => {
    const src = makeSchemaInfo({
      fields: [
        makeFieldInfo({ name: "txn_id", dtype: "string", sampleValues: ["1", "2"], valueCount: 2 }),
        makeFieldInfo({ name: "amt", dtype: "float", sampleValues: ["1.0", "2.0"], valueCount: 2 }),
        makeFieldInfo({ name: "ccy", dtype: "string", sampleValues: ["USD", "EUR"], valueCount: 2 }),
      ],
    });
    const tgt = makeSchemaInfo({
      fields: [
        makeFieldInfo({ name: "transaction_id", dtype: "string", sampleValues: ["x", "y"], valueCount: 2 }),
        makeFieldInfo({ name: "amount", dtype: "float", sampleValues: ["10.0", "20.0"], valueCount: 2 }),
        makeFieldInfo({ name: "currency", dtype: "string", sampleValues: ["GBP", "JPY"], valueCount: 2 }),
      ],
    });
    const result = new MapEngine({ domains: ["finance"] }).mapSchemas(src, tgt);
    const pairs = new Set(result.mappings.map((m) => `${m.source}->${m.target}`));
    expect(pairs.has("txn_id->transaction_id")).toBe(true);
    expect(pairs.has("amt->amount")).toBe(true);
    expect(pairs.has("ccy->currency")).toBe(true);
  });

  it("ecommerce domain knows sku <-> product_id", () => {
    const src = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "sku", dtype: "string", sampleValues: ["X1", "X2"], valueCount: 2 })],
    });
    const tgt = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "product_id", dtype: "string", sampleValues: ["P1", "P2"], valueCount: 2 })],
    });
    const result = new MapEngine({ domains: ["ecommerce"] }).mapSchemas(src, tgt);
    expect(result.mappings.length).toBeGreaterThan(0);
    expect(result.mappings[0]!.source).toBe("sku");
    expect(result.mappings[0]!.target).toBe("product_id");
  });

  it("explicit generic in the domains list is not duplicated", () => {
    const src = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "email", dtype: "string", sampleValues: ["a@b"], valueCount: 1 })],
    });
    const tgt = makeSchemaInfo({
      fields: [makeFieldInfo({ name: "e_mail", dtype: "string", sampleValues: ["c@d"], valueCount: 1 })],
    });
    const r1 = new MapEngine({ domains: ["healthcare"] }).mapSchemas(src, tgt);
    const r2 = new MapEngine({ domains: ["generic", "healthcare"] }).mapSchemas(src, tgt);
    expect(r1.mappings.length).toBe(1);
    expect(r2.mappings.length).toBe(1);
    expect(r1.mappings[0]!.confidence).toBeCloseTo(r2.mappings[0]!.confidence, 9);
  });

  it("throws UnknownDomainError for an unknown domain", () => {
    expect(() => new MapEngine({ domains: ["not_a_real_domain"] })).toThrow(UnknownDomainError);
  });
});
