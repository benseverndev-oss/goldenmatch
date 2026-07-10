import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { profileComplexity } from "../../src/core/autoconfigGlue.js";
import type { Row } from "../../src/core/index.js";

const VEC = fileURLToPath(
  new URL(
    "../../../../rust/extensions/goldenpipe-core/tests/vectors/profile_complexity.json",
    import.meta.url,
  ),
);

interface Case {
  comment?: string;
  input?: { rows: Row[] };
  expected?: { max_null_density: number; mean_null_density: number };
}

const cases = (JSON.parse(readFileSync(VEC, "utf8")) as Case[]).filter((c) => c.input);

describe("profileComplexity == goldenpipe-core profile_complexity vector", () => {
  for (const c of cases) {
    it(c.comment ?? "case", () => {
      const comp = profileComplexity(c.input!.rows);
      expect(comp.maxNullDensity).toBeCloseTo(c.expected!.max_null_density, 10);
      expect(comp.meanNullDensity).toBeCloseTo(c.expected!.mean_null_density, 10);
    });
  }
});
