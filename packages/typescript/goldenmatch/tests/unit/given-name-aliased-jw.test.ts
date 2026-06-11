import { describe, it, expect } from "vitest";
import { scoreField, scoreMatrix, jaroWinkler } from "../../src/core/scorer.js";
import { VALID_SCORERS } from "../../src/core/types.js";

const S = "given_name_aliased_jw";

describe("given_name_aliased_jw scorer", () => {
  it("is a valid scorer name", () => {
    expect(VALID_SCORERS.has(S)).toBe(true);
  });
  it("alias pair scores 1.0", () => {
    expect(scoreField("William", "Bill", S)).toBe(1.0);
    expect(scoreField("Bob", "Robert", S)).toBe(1.0);
  });
  it("non-alias pair falls back to jaro_winkler", () => {
    expect(scoreField("Robert", "William", S)).toBeCloseTo(
      jaroWinkler("Robert", "William"),
      10,
    );
  });
  it("null propagates", () => {
    expect(scoreField(null, "Bill", S)).toBeNull();
  });
  it("matrix path promotes alias pairs to 1.0 (generic per-pair route)", () => {
    const m = scoreMatrix(["William", "Bill", "Robert"], S);
    expect(m[0]![1]).toBe(1.0); // William ~ Bill
    expect(m[0]![2]).toBeCloseTo(jaroWinkler("William", "Robert"), 10);
  });
});
