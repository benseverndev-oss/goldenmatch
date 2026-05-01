import { describe, it, expect } from "vitest";
import { loadDomain } from "goldencheck-types";
import { DomainPackTarget, detectDomain, map as infermapMap } from "../src/core/index.js";

describe("DomainPackTarget", () => {
  it("converts a finance pack to a SchemaInfo", () => {
    const pack = loadDomain("finance");
    const tgt = new DomainPackTarget(pack);
    const schema = tgt.toSchemaInfo();
    expect(schema.sourceName).toBe("domain:finance");
    const names = new Set(schema.fields.map((f) => f.name));
    expect(names.has("account_number")).toBe(true);
    const acct = schema.fields.find((f) => f.name === "account_number")!;
    expect(acct.sampleValues.length).toBeGreaterThan(0);
    expect(acct.sampleValues.some((s) => s.includes("account"))).toBe(true);
  });
});

describe("map() with DomainPackTarget", () => {
  it("returns a MapResult", () => {
    const pack = loadDomain("finance");
    const records = [
      { account_number: "A1234", currency: "USD" },
      { account_number: "A5678", currency: "EUR" },
    ];
    const result = infermapMap(
      { records, sourceName: "src" },
      new DomainPackTarget(pack),
    );
    expect(result.mappings).toBeDefined();
  });

  it("soft mode marks low-confidence as target=null", () => {
    const pack = loadDomain("finance");
    const records = [
      { account_number: "A1234", totally_random_xyz: "zzz" },
      { account_number: "A5678", totally_random_xyz: "qqq" },
    ];
    const result = infermapMap(
      { records, sourceName: "src" },
      new DomainPackTarget(pack),
      { soft: true },
    );
    const bySrc: Record<string, any> = {};
    for (const m of result.mappings) bySrc[m.source] = m;
    if ("totally_random_xyz" in bySrc) {
      expect(bySrc.totally_random_xyz.target).toBeNull();
    }
  });
});

describe("detectDomain", () => {
  it("detects finance from columns", () => {
    expect(
      detectDomain({ columns: ["account_number", "routing", "currency"] }),
    ).toBe("finance");
  });

  it("detects healthcare from columns", () => {
    expect(
      detectDomain({ columns: ["patient_id", "diagnosis", "icd10"] }),
    ).toBe("healthcare");
  });

  it("returns null when no match", () => {
    expect(detectDomain({ columns: ["foo", "bar", "baz"] })).toBeNull();
  });
});
