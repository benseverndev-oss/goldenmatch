/**
 * FS negative-evidence loud decline (TS port phase 3, first commit).
 *
 * Every FS entry point must THROW `NegativeEvidenceUnsupportedError` on a
 * probabilistic matchkey carrying non-empty `negativeEvidence` — killing the
 * silent-wrong-scores state where a Python-authored NE config scored in TS
 * without the veto. The discrete-path throws are lifted later in this branch
 * as the port lands; the continuous (Winkler) path throws PERMANENTLY,
 * matching Python.
 *
 * Weighted/exact NE behavior is untouched (guard test at the bottom).
 */
import { describe, it, expect } from "vitest";
import {
  trainEM,
  scoreProbabilistic,
  scoreProbabilisticPair,
  validateEmResultFor,
  trainEMContinuous,
  scoreProbabilisticContinuous,
} from "../../src/core/probabilistic.js";
import type { EMResult, ContinuousEMResult } from "../../src/core/probabilistic.js";
import { parseConfig } from "../../src/core/config/loader.js";
import { findFuzzyMatches } from "../../src/core/scorer.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type Row,
} from "../../src/core/types.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const rows: Row[] = [
  { __row_id__: 0, name: "Alice Smith", phone: "555-1111" } as Row,
  { __row_id__: 1, name: "Alice Smith", phone: "555-9999" } as Row,
];

function neProbMatchkey() {
  return makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [makeMatchkeyField({ field: "name", scorer: "jaro_winkler" })],
    negativeEvidence: [
      makeNegativeEvidenceField({
        field: "phone",
        scorer: "exact",
        threshold: 0.5,
      }),
    ],
  });
}

// The guards must fire BEFORE the model is touched, so dummies suffice.
const dummyEm = {} as EMResult;
const dummyCem = {} as ContinuousEMResult;

function expectNeDecline(fn: () => unknown): void {
  let thrown: unknown;
  try {
    fn();
  } catch (err) {
    thrown = err;
  }
  expect(thrown).toBeInstanceOf(Error);
  const e = thrown as Error;
  expect(e.name).toBe("NegativeEvidenceUnsupportedError");
  expect(e.message).toContain("phone");
}

// ---------------------------------------------------------------------------
// The six entry points
// ---------------------------------------------------------------------------

describe("FS negative-evidence loud decline (probabilistic matchkeys)", () => {
  it("trainEM throws", () => {
    expectNeDecline(() => trainEM(rows, neProbMatchkey()));
  });

  it("scoreProbabilistic throws", () => {
    expectNeDecline(() => scoreProbabilistic(rows, neProbMatchkey(), dummyEm));
  });

  it("scoreProbabilisticPair throws", () => {
    expectNeDecline(() =>
      scoreProbabilisticPair(rows[0]!, rows[1]!, neProbMatchkey(), dummyEm),
    );
  });

  it("validateEmResultFor throws", () => {
    expectNeDecline(() => validateEmResultFor(dummyEm, neProbMatchkey()));
  });

  it("trainEMContinuous throws (permanent)", () => {
    expectNeDecline(() => trainEMContinuous(rows, neProbMatchkey()));
  });

  it("scoreProbabilisticContinuous throws (permanent)", () => {
    expectNeDecline(() =>
      scoreProbabilisticContinuous(rows, neProbMatchkey(), dummyCem),
    );
  });

  it("empty negativeEvidence list does NOT throw", () => {
    const mk = makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [makeMatchkeyField({ field: "name", scorer: "jaro_winkler" })],
      negativeEvidence: [],
    });
    // Tiny dataset -> fallback EMResult; the point is: no NE throw.
    expect(() => trainEM(rows, mk)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Loader + decline compose (the fan-out-lever YAML hazard): a loaded
// probabilistic+NE config must reach the throw, not silently score.
// ---------------------------------------------------------------------------

describe("loader-parsed NE config hits the decline", () => {
  it("parseConfig -> scoreProbabilistic throws", () => {
    const raw = {
      matchkeys: [
        {
          name: "p",
          type: "probabilistic",
          fields: [
            { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 },
          ],
          negative_evidence: [
            {
              field: "phone",
              transforms: ["digits_only"],
              scorer: "exact",
              threshold: 0.5,
              penalty_bits: 2.5,
            },
          ],
        },
      ],
    };
    const config = parseConfig(raw);
    const mk = config.matchkeys?.[0];
    expect(mk?.type).toBe("probabilistic");
    expectNeDecline(() => scoreProbabilistic(rows, mk!, dummyEm));
  });
});

// ---------------------------------------------------------------------------
// Weighted NE untouched: the existing scorer path still works.
// ---------------------------------------------------------------------------

describe("weighted NE scoring is untouched by the decline", () => {
  it("weighted matchkey with NE still scores via findFuzzyMatches", () => {
    const wRows: Row[] = [
      { __row_id__: 0, name: "Alice Smith", phone: "555-1111" } as Row,
      { __row_id__: 1, name: "Alice Smith", phone: "555-1111" } as Row,
      { __row_id__: 2, name: "Alice Smith", phone: "555-9999" } as Row,
    ];
    const mk = makeMatchkeyConfig({
      name: "w",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
      threshold: 0.9,
      negativeEvidence: [
        makeNegativeEvidenceField({
          field: "phone",
          scorer: "exact",
          threshold: 0.5,
          penalty: 0.5,
        }),
      ],
    });
    const out = findFuzzyMatches(wRows, mk);
    // Only (0,1) survives: phone agreement. Phone-disagreeing pairs penalized.
    expect(out.length).toBe(1);
    expect(out[0]?.idA).toBe(0);
    expect(out[0]?.idB).toBe(1);
  });
});
