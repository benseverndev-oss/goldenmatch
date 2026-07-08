import { describe, it, expect } from "vitest";
import { repairTransformSpecs, mergeTransforms, FIXERS } from "../../src/core/repairHost.js";

describe("repairTransformSpecs", () => {
  it("keeps fixers grouped+deduped, skips assertions", () => {
    const plan = { repairs: [
      { column: "email", check: "format_detection", type_tag: "email", suggested_transforms: ["email_normalize"], reason: "x" },
      { column: "iban", check: "pattern_consistency", type_tag: "iban", suggested_transforms: ["iban_validate"], reason: "b" },
    ] };
    const { specs, skipped } = repairTransformSpecs(plan as never);
    expect(specs).toEqual([{ column: "email", ops: ["email_normalize"] }]);
    expect(skipped).toEqual([{ column: "iban", op: "iban_validate" }]);
  });
  it("FIXERS excludes validators", () => {
    expect(FIXERS.has("email_normalize")).toBe(true);
    expect(FIXERS.has("iban_validate")).toBe(false);
  });
  it("merges user-first then repair, deduped", () => {
    const merged = mergeTransforms(
      [{ column: "email", ops: ["email_lowercase"] }],
      [{ column: "email", ops: ["email_normalize", "email_lowercase"] }],
    );
    expect(merged).toEqual([{ column: "email", ops: ["email_lowercase", "email_normalize"] }]);
  });
});
