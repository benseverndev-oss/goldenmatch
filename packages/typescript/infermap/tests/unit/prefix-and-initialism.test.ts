// Tests for common-affix canonicalization + InitialismScorer.
// Mirrors tests/test_prefix_and_initialism.py.
import { describe, it, expect } from "vitest";
import {
  MapEngine,
  commonAffixTokens,
  populateCanonicalNames,
} from "../../src/core/engine.js";
import {
  InitialismScorer,
  isPrefixConcat,
  scorePair,
  tokenize,
} from "../../src/core/scorers/initialism.js";
import { makeFieldInfo, makeSchemaInfo } from "../../src/core/types.js";

const f = (name: string, samples: string[] = ["a", "b", "c"]) =>
  makeFieldInfo({ name, dtype: "string", sampleValues: samples, valueCount: samples.length });

// --- commonAffixTokens ------------------------------------------------------

describe("commonAffixTokens", () => {
  it("finds common prefix at delimiter boundary", () => {
    expect(
      commonAffixTokens(["prospect_City", "prospect_Employer", "prospect_Phone"], true)
    ).toBe("prospect_");
  });

  it("rejects mid-token overlap", () => {
    expect(commonAffixTokens(["email", "employee", "employer"], true)).toBe("");
  });

  it("finds common suffix", () => {
    expect(commonAffixTokens(["email_id", "customer_id", "order_id"], false)).toBe("_id");
  });

  it("returns empty for a single field", () => {
    expect(commonAffixTokens(["alone"], true)).toBe("");
  });
});

describe("populateCanonicalNames", () => {
  it("strips common prefix", () => {
    const fields = [f("prospect_City"), f("prospect_Employer"), f("prospect_Phone")];
    populateCanonicalNames(fields);
    expect(fields.map((x) => x.canonicalName)).toEqual(["City", "Employer", "Phone"]);
  });

  it("leaves names unchanged when there's no shared affix", () => {
    const fields = [f("foo"), f("bar")];
    populateCanonicalNames(fields);
    expect(fields.map((x) => x.canonicalName)).toEqual(["foo", "bar"]);
  });
});

describe("MapEngine deep-copy contract", () => {
  it("does not mutate caller's input schemas", () => {
    const src = makeSchemaInfo({ fields: [f("prospect_City"), f("prospect_Employer")] });
    const tgt = makeSchemaInfo({ fields: [f("City"), f("Employer")] });
    new MapEngine().mapSchemas(src, tgt);
    for (const fd of src.fields) expect(fd.canonicalName).toBeUndefined();
    for (const fd of tgt.fields) expect(fd.canonicalName).toBeUndefined();
  });
});

// --- tokenizer --------------------------------------------------------------

describe("tokenize", () => {
  it("splits snake_case", () => {
    expect(tokenize("assay_id")).toEqual(["assay", "id"]);
  });
  it("splits PascalCase", () => {
    expect(tokenize("CustomerId")).toEqual(["customer", "id"]);
  });
  it("splits mixed", () => {
    expect(tokenize("relationship_Type")).toEqual(["relationship", "type"]);
  });
});

// --- prefix-concat matcher --------------------------------------------------

describe("isPrefixConcat", () => {
  it("matches positive cases", () => {
    expect(isPrefixConcat("assi", ["assay", "id"])).toBe(true);
    expect(isPrefixConcat("consc", ["confidence", "score"])).toBe(true);
    expect(isPrefixConcat("relatit", ["relationship", "type"])).toBe(true);
  });

  it("rejects negative cases", () => {
    expect(isPrefixConcat("xyz", ["assay", "id"])).toBe(false);
    expect(isPrefixConcat("celid", ["assay", "id"])).toBe(false);
  });
});

// --- scorePair --------------------------------------------------------------

describe("scorePair", () => {
  it("scores known abbreviation pairs above 0.6", () => {
    for (const [a, b] of [
      ["assay_id", "ASSI"],
      ["confidence_score", "CONSC"],
      ["relationship_type", "RELATIT"],
      ["curated_by", "CURAB"],
    ]) {
      const s = scorePair(a!, b!);
      expect(s).not.toBeNull();
      expect(s!).toBeGreaterThan(0.6);
    }
  });

  it("abstains on unrelated names", () => {
    expect(scorePair("city", "employer")).toBeNull();
    expect(scorePair("email", "phone")).toBeNull();
  });

  it("abstains when names match (other scorers handle these)", () => {
    expect(scorePair("assay_id", "assay_id")).toBeNull();
  });
});

describe("InitialismScorer", () => {
  it("uses canonicalName when set", () => {
    const src = makeFieldInfo({ name: "prefix_assay_id" });
    src.canonicalName = "assay_id";
    const tgt = makeFieldInfo({ name: "ASSI" });
    const result = new InitialismScorer().score(src, tgt);
    expect(result).not.toBeNull();
    expect(result!.score).toBeGreaterThan(0.6);
  });
});

// --- end-to-end via MapEngine -----------------------------------------------

describe("end-to-end regression guards", () => {
  it("prefix-strip fixes the prospect_City near-tie", () => {
    const src = makeSchemaInfo({
      fields: [
        f("City", ["NYC", "LA", "SF"]),
        f("Employer", ["Acme", "Widgets", "Co"]),
      ],
    });
    const tgt = makeSchemaInfo({
      fields: [
        f("prospect_City", ["Boston", "Austin", "Seattle"]),
        f("prospect_Employer", ["Foo", "Bar", "Baz"]),
        f("prospect_Phone", ["555", "444", "333"]),
      ],
    });
    const result = new MapEngine().mapSchemas(src, tgt);
    const pairs = new Set(result.mappings.map((m) => `${m.source}->${m.target}`));
    expect(pairs.has("City->prospect_City")).toBe(true);
    expect(pairs.has("Employer->prospect_Employer")).toBe(true);
  });

  it("InitialismScorer fixes ChEMBL ASSI / RELATIT cases", () => {
    const src = makeSchemaInfo({
      fields: [
        makeFieldInfo({ name: "assay_id", dtype: "integer", sampleValues: ["1", "2", "3"], valueCount: 3 }),
        makeFieldInfo({ name: "relationship_type", dtype: "string", sampleValues: ["a", "b", "c"], valueCount: 3 }),
      ],
    });
    const tgt = makeSchemaInfo({
      fields: [
        makeFieldInfo({ name: "ASSI", dtype: "integer", sampleValues: ["10", "20", "30"], valueCount: 3 }),
        makeFieldInfo({ name: "RELATIT", dtype: "string", sampleValues: ["x", "y", "z"], valueCount: 3 }),
      ],
    });
    const result = new MapEngine().mapSchemas(src, tgt);
    const pairs = new Set(result.mappings.map((m) => `${m.source}->${m.target}`));
    expect(pairs.has("assay_id->ASSI")).toBe(true);
    expect(pairs.has("relationship_type->RELATIT")).toBe(true);
  });
});
