/**
 * cli-certify-keys.test.ts — TS CLI coverage for the `certify-keys` command.
 *
 * Per repo convention (cli-parity-batch / cli-memory / cli-evaluate) we test the
 * command-local logic (`rowsToColumns` frame pivot, `fmtG` float format) plus the
 * resolve-routing contract the command wraps, rather than driving the commander
 * tree. The resolution-tier + metric-aware semantics themselves are locked in
 * semantic-resolve-tier / semantic-metric-aware / semantic-roles parity tests.
 */
import { describe, it, expect } from "vitest";

import { rowsToColumns, fmtG } from "../../src/cli.js";
import { certifySemanticModel } from "../../src/core/semantic/certify.js";
import { certifySemanticModelResolved } from "../../src/core/semantic/certify.js";
import type { SemanticFrames } from "../../src/core/semantic/frame.js";

describe("certify-keys command-local logic", () => {
  describe("rowsToColumns — CSV rows → column frame pivot", () => {
    it("pivots rows to columns, unioning keys across rows", () => {
      const cols = rowsToColumns([
        { id: "1", name: "A" },
        { id: "2", name: "B" },
      ]);
      expect(cols["id"]).toEqual(["1", "2"]);
      expect(cols["name"]).toEqual(["A", "B"]);
    });

    it("fills a missing column with null (ragged rows)", () => {
      const cols = rowsToColumns([{ id: "1", email: "a@x.com" }, { id: "2" }]);
      expect(cols["id"]).toEqual(["1", "2"]);
      expect(cols["email"]).toEqual(["a@x.com", null]);
    });

    it("is null-prototype — a __proto__ column is captured as data, not prototype-mutating", () => {
      // Computed key so `__proto__` is a real own enumerable column name (a hostile
      // header), NOT prototype-mutating object-literal syntax (which JS ignores for a
      // string value, so the pivot would never even see the column).
      const cols = rowsToColumns([{ ["__proto__"]: "x", id: "1" } as Record<string, unknown>]);
      // The pivot result has no prototype, so the crafted column is an inert own key.
      expect(Object.getPrototypeOf(cols)).toBeNull();
      expect(Object.prototype.hasOwnProperty.call(cols, "__proto__")).toBe(true);
      expect((cols as Record<string, unknown>)["__proto__"]).toEqual(["x"]);
      // A plain object's prototype is untouched by having pivoted a __proto__ column.
      expect(Object.getPrototypeOf({})).toBe(Object.prototype);
    });

    it("empty input → empty frame", () => {
      expect(rowsToColumns([])).toEqual({});
    });
  });

  describe("fmtG — Python f\"{x:g}\" float format", () => {
    it("drops the decimal point for integer-valued fan-outs", () => {
      expect(fmtG(1)).toBe("1");
      expect(fmtG(2)).toBe("2");
    });
    it("keeps significant fractional digits, stripping trailing zeros", () => {
      expect(fmtG(2.5)).toBe("2.5");
      expect(fmtG(1.5)).toBe("1.5");
      expect(fmtG(1.25)).toBe("1.25");
    });
  });
});

describe("certify-keys resolve routing (the contract the command branches on)", () => {
  const model = {
    semantic_models: [
      {
        name: "customers",
        entities: [{ name: "customer", type: "primary", expr: "customer_id" }],
        dimensions: [{ name: "email" }, { name: "name" }],
        measures: [{ name: "revenue", agg: "sum", expr: "revenue" }],
      },
    ],
  };
  const frames: SemanticFrames = {
    customers: {
      customer_id: [1, 2, 3, 4],
      email: ["alice@x.com", "alice@x.com", "bob@y.com", "carol@z.com"],
      name: ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
      revenue: [10, 20, 30, 40],
    },
  };

  it("default (no --resolve) → structural tier: resolve fields stay null", () => {
    const report = certifySemanticModel(model, frames);
    const cert = report.entries[0]!.certificate;
    expect(cert.resolvedEntities).toBeNull();
    expect(cert.undercountEstimate).toBeNull();
    // The command prints certificate.note; structural note carries no resolution text.
    expect(cert.note).not.toContain("fragmentation");
  });

  it("--resolve → resolution tier: fields populated and the note surfaces fragmentation", async () => {
    const report = await certifySemanticModelResolved(model, frames);
    const cert = report.entries[0]!.certificate;
    expect(cert.resolvedEntities).not.toBeNull();
    expect(cert.fragmentedEntities as number).toBeGreaterThanOrEqual(1);
    // This is exactly the per-entry note the CLI prints below the table under --resolve.
    expect(cert.note).toContain("fragmentation");
  }, 20000);
});
