/**
 * Wave-3 negative-evidence parity test.
 *
 * Fixture format (per dataset):
 *   {
 *     name,
 *     input_rows,
 *     before:                  { matchkeys: [...] },
 *     expected_after:          { matchkeys: [...] },  // after promote_negative_evidence
 *     expected_after_idempotent: { matchkeys: [...] }
 *   }
 *
 * For each fixture we build the same synthetic GoldenMatchConfig as the
 * Python emitter, run ``promoteNegativeEvidence`` (TS port), and assert
 * matchkey names + NE field shapes match. Idempotency is asserted by
 * running promote a second time and re-comparing.
 *
 * Numeric tolerances: threshold + penalty are compared at 4dp.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { promoteNegativeEvidence } from "../../src/core/autoconfigNegativeEvidence.js";
import { computeColumnPriors } from "../../src/core/indicators.js";
import type {
  GoldenMatchConfig,
  MatchkeyConfig,
  Row,
} from "../../src/core/types.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
} from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

interface PyFieldShape {
  field: string;
  transforms: string[];
  scorer: string;
  weight: number | null;
}
interface PyNeShape {
  field: string;
  transforms: string[];
  scorer: string;
  threshold: number;
  penalty: number;
}
interface PyMkShape {
  name: string;
  type: "exact" | "weighted" | "probabilistic";
  fields: PyFieldShape[];
  threshold?: number;
  negative_evidence: PyNeShape[];
}
interface PyFixture {
  name: string;
  input_rows: Record<string, unknown>[];
  before: { matchkeys: PyMkShape[] };
  expected_after: { matchkeys: PyMkShape[] };
  expected_after_idempotent: { matchkeys: PyMkShape[] };
}

const fixturesPath = join(__dirname, "negative-evidence-fixtures.json");
const fixtures = JSON.parse(readFileSync(fixturesPath, "utf-8")) as Record<
  string,
  PyFixture
>;

function buildTsConfig(fx: PyFixture): GoldenMatchConfig {
  const matchkeys: MatchkeyConfig[] = fx.before.matchkeys.map((mk) =>
    makeMatchkeyConfig({
      name: mk.name,
      type: mk.type,
      fields: mk.fields.map((f) =>
        makeMatchkeyField({
          field: f.field,
          transforms: f.transforms,
          scorer: f.scorer,
          weight: f.weight ?? 1.0,
        }),
      ),
      ...(mk.threshold !== undefined ? { threshold: mk.threshold } : {}),
    }),
  );

  // Blocking shape: parity-target Python sets blocking on the first user col
  // (or "phone" for the ne_blocking_field_skipped case). Mirror that.
  const firstCol = Object.keys(fx.input_rows[0] ?? {})[0]!;
  const blockingField =
    fx.name === "ne_blocking_field_skipped" ? "phone" : firstCol;

  return {
    matchkeys,
    blocking: {
      strategy: "static",
      keys: [{ fields: [blockingField], transforms: [] }],
      maxBlockSize: 5000,
      skipOversized: false,
    },
  };
}

function neFieldShape(ne: {
  field: string;
  transforms: readonly string[];
  scorer: string;
  threshold: number;
  penalty: number;
}): PyNeShape {
  return {
    field: ne.field,
    transforms: [...ne.transforms],
    scorer: ne.scorer,
    threshold: Number(ne.threshold.toFixed(4)),
    penalty: Number(ne.penalty.toFixed(4)),
  };
}

function mkAfterShape(mk: MatchkeyConfig): {
  name: string;
  type: string;
  ne: PyNeShape[];
} {
  const ne = (
    (mk as unknown as {
      negativeEvidence?: ReadonlyArray<{
        field: string;
        transforms: readonly string[];
        scorer: string;
        threshold: number;
        penalty: number;
      }>;
    }).negativeEvidence ?? []
  ).map((n) =>
    neFieldShape({
      field: n.field,
      transforms: n.transforms,
      scorer: n.scorer,
      threshold: n.threshold,
      penalty: n.penalty,
    }),
  );
  // Sort NE fields by name for stable comparison
  ne.sort((a, b) => a.field.localeCompare(b.field));
  return { name: mk.name, type: mk.type, ne };
}

function pyMkAfterShape(mk: PyMkShape): {
  name: string;
  type: string;
  ne: PyNeShape[];
} {
  const ne = mk.negative_evidence.map((n) => neFieldShape(n));
  ne.sort((a, b) => a.field.localeCompare(b.field));
  return { name: mk.name, type: mk.type, ne };
}

describe("negative-evidence Python parity (Wave 3)", () => {
  for (const [name, fx] of Object.entries(fixtures)) {
    it(`${name}: promoteNegativeEvidence matches Python`, () => {
      const cfg = buildTsConfig(fx);
      const rows: Row[] = fx.input_rows.map((r, i) => ({
        __row_id__: i,
        ...r,
      })) as Row[];
      const priors = computeColumnPriors(rows);
      const promoted = promoteNegativeEvidence(cfg, rows, priors);

      const tsShape = (promoted.matchkeys ?? []).map(mkAfterShape);
      const pyShape = fx.expected_after.matchkeys.map(pyMkAfterShape);
      expect(tsShape).toEqual(pyShape);

      // Idempotency: running twice yields same shape
      const twice = promoteNegativeEvidence(promoted, rows, priors);
      const tsTwice = (twice.matchkeys ?? []).map(mkAfterShape);
      const pyTwice = fx.expected_after_idempotent.matchkeys.map(pyMkAfterShape);
      expect(tsTwice).toEqual(pyTwice);
    });
  }
});
