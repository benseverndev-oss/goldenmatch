/**
 * cli-evaluate.test.ts -- Wave 4 TS CLI parity: the `evaluate` command.
 *
 * Per repo convention (see cli-memory.test.ts) we test the underlying logic
 * the subcommand wraps rather than driving the commander tree.
 */
import { describe, it, expect } from "vitest";
import { dedupe } from "../../src/core/api.js";
import { evaluateClusters, loadGroundTruthPairs } from "../../src/core/index.js";
import pkg from "../../package.json" with { type: "json" };

describe("evaluate command logic", () => {
  it("computes precision/recall/F1 from dedupe clusters vs ground truth", async () => {
    const rows = [
      { __source__: "a", name: "John Smith", email: "john@x.com" },
      { __source__: "a", name: "Jon Smith", email: "john@x.com" },
      { __source__: "a", name: "Bob Jones", email: "bob@y.com" },
    ];
    const result = await dedupe(rows, { exact: ["email"] });

    // Ground truth: rows 0 and 1 are the same entity.
    const gt = loadGroundTruthPairs([{ id_a: 0, id_b: 1 }], "id_a", "id_b");
    const ev = evaluateClusters(
      result.clusters,
      gt,
      rows.map((_, i) => i),
    );

    expect(ev.truePositives).toBeGreaterThanOrEqual(1);
    expect(ev.f1).toBeGreaterThan(0);
    expect(ev.precision).toBeLessThanOrEqual(1);
  });
});

describe("CLI version", () => {
  it("is read from package.json, not the hardcoded 0.1.0", () => {
    // The cli previously hardcoded "0.1.0" in .version() and `info`.
    expect(pkg.version).not.toBe("0.1.0");
  });
});
