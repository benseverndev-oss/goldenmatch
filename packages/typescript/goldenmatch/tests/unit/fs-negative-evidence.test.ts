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
  neFired,
  fsWeightRange,
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

// ---------------------------------------------------------------------------
// T3: neFired — the FS NE firing rule (mirrors Python `_ne_fired`).
// ---------------------------------------------------------------------------

describe("neFired", () => {
  it("fires with exact scorer when both present and disagree", () => {
    const ne = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      threshold: 0.5,
    });
    const a = { phone: "555-1111" } as Row;
    const b = { phone: "555-9999" } as Row;
    expect(neFired(a, b, ne)).toBe(true);
  });

  it("fires with a fuzzy scorer (jaro_winkler) below threshold", () => {
    const ne = makeNegativeEvidenceField({
      field: "surname",
      scorer: "jaro_winkler",
      threshold: 0.8,
    });
    const a = { surname: "smith" } as Row;
    const b = { surname: "jones" } as Row;
    expect(neFired(a, b, ne)).toBe(true);
  });

  it("applies transforms before scoring (lowercase suppresses a case-only diff)", () => {
    const ne = makeNegativeEvidenceField({
      field: "email",
      scorer: "exact",
      threshold: 0.5,
      transforms: ["lowercase"],
    });
    const a = { email: "ALICE@X.COM" } as Row;
    const b = { email: "alice@x.com" } as Row;
    // Without the transform, exact would give 0 -> fire. With lowercase,
    // sim = 1.0 -> no fire. Proves the transform chain runs.
    expect(neFired(a, b, ne)).toBe(false);
    const neNoTransform = makeNegativeEvidenceField({
      field: "email",
      scorer: "exact",
      threshold: 0.5,
    });
    expect(neFired(a, b, neNoTransform)).toBe(true);
  });

  it("does NOT fire at sim == threshold (STRICT <)", () => {
    const ne = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      threshold: 1.0,
    });
    const a = { phone: "555-1111" } as Row;
    const b = { phone: "555-1111" } as Row;
    // exact + identical -> sim 1.0; 1.0 < 1.0 is false -> no fire.
    expect(neFired(a, b, ne)).toBe(false);
  });

  it("does NOT fire when either side is null/undefined", () => {
    const ne = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      threshold: 0.5,
    });
    expect(neFired({ phone: null } as Row, { phone: "555-9999" } as Row, ne)).toBe(false);
    expect(neFired({ phone: "555-1111" } as Row, {} as Row, ne)).toBe(false);
  });

  it("does NOT fire when a value is empty after transforms", () => {
    const ne = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      threshold: 0.5,
      transforms: ["digits_only"],
    });
    // "-" -> "" after digits_only: inconclusive, must not fire.
    expect(neFired({ phone: "-" } as Row, { phone: "5559999" } as Row, ne)).toBe(false);
    // Bare empty string, no transforms.
    const nePlain = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      threshold: 0.5,
    });
    expect(neFired({ phone: "" } as Row, { phone: "5559999" } as Row, nePlain)).toBe(false);
  });

  it("does NOT fire on an unknown scorer (defensive skip)", () => {
    const ne = makeNegativeEvidenceField({
      field: "phone",
      scorer: "no_such_scorer_xyz",
      threshold: 0.5,
    });
    expect(neFired({ phone: "a" } as Row, { phone: "b" } as Row, ne)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// T3: fsWeightRange — the normalization envelope (mirrors Python
// `fs_weight_range`).
// ---------------------------------------------------------------------------

function makeEm(matchWeights: Record<string, readonly number[]>): EMResult {
  return {
    m: {},
    u: {},
    matchWeights,
    proportionMatched: 0.1,
    iterations: 1,
    converged: true,
  };
}

function probMk(
  negativeEvidence?: ReturnType<typeof makeNegativeEvidenceField>[],
) {
  return makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [
      makeMatchkeyField({ field: "name", scorer: "exact" }),
      makeMatchkeyField({ field: "city", scorer: "exact" }),
    ],
    ...(negativeEvidence !== undefined ? { negativeEvidence } : {}),
  });
}

describe("fsWeightRange", () => {
  const weights = { name: [-2, 3], city: [-1, 2] };

  it("no-NE matchkey equals the hand-rolled reduce computation", () => {
    const em = makeEm(weights);
    const mk = probMk();
    // Expected via the same reduce semantics the scoring sites used.
    let expMax = 0;
    let expMin = 0;
    for (const f of mk.fields) {
      const w = em.matchWeights[f.field];
      if (!w || w.length === 0) continue;
      expMax += w.reduce((m, v) => (v > m ? v : m), -Infinity);
      expMin += w.reduce((m, v) => (v < m ? v : m), Infinity);
    }
    const { minWeight, maxWeight } = fsWeightRange(em, mk);
    expect(minWeight).toBe(expMin); // -3
    expect(maxWeight).toBe(expMax); // 5
  });

  it("__ne__ entry [-4, 0] extends min by -4, max unchanged", () => {
    const em = makeEm({ ...weights, __ne__phone: [-4, 0] });
    const mk = probMk([
      makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 0.5 }),
    ]);
    const { minWeight, maxWeight } = fsWeightRange(em, mk);
    expect(minWeight).toBe(-3 + -4);
    expect(maxWeight).toBe(5);
  });

  it("penaltyBits: 3 contributes (-3, 0) without needing an EM entry", () => {
    const em = makeEm(weights);
    const mk = probMk([
      makeNegativeEvidenceField({
        field: "phone",
        scorer: "exact",
        threshold: 0.5,
        penaltyBits: 3,
      }),
    ]);
    const { minWeight, maxWeight } = fsWeightRange(em, mk);
    expect(minWeight).toBe(-3 + -3);
    expect(maxWeight).toBe(5);
  });

  it("NE field with no __ne__ entry and no penaltyBits is skipped", () => {
    const em = makeEm(weights);
    const mk = probMk([
      makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 0.5 }),
    ]);
    const { minWeight, maxWeight } = fsWeightRange(em, mk);
    expect(minWeight).toBe(-3);
    expect(maxWeight).toBe(5);
  });

  it("regular field with missing/empty weights entry is skipped (no NaN/Infinity)", () => {
    const em = makeEm({ name: [-2, 3], city: [] });
    const mk = probMk();
    const { minWeight, maxWeight } = fsWeightRange(em, mk);
    expect(minWeight).toBe(-2);
    expect(maxWeight).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// T3 regression pin: the fsWeightRange swap must not move any no-NE score.
// Values below were produced by the PRE-SWAP hand-rolled min/max blocks.
// ---------------------------------------------------------------------------

describe("scoring regression pin (no-NE configs unchanged by fsWeightRange swap)", () => {
  const pinRows: Row[] = [
    { __row_id__: 0, name: "alice", city: "nyc" } as Row,
    { __row_id__: 1, name: "alice", city: "sf" } as Row,
    { __row_id__: 2, name: "bob", city: "nyc" } as Row,
  ];
  const em = {
    m: {},
    u: {},
    matchWeights: { name: [-2, 3], city: [-1, 2] },
    proportionMatched: 0.1,
    iterations: 1,
    converged: true,
  } as EMResult;
  const mk = makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [
      makeMatchkeyField({ field: "name", scorer: "exact" }),
      makeMatchkeyField({ field: "city", scorer: "exact" }),
    ],
  });

  it("scoreProbabilistic pins", () => {
    // range = [-3, 5]; (0,1): 3-1=2 -> (2+3)/8 = 0.625 (>= 0.5 default);
    // (0,2): -2+2=0 -> 0.375 dropped; (1,2): -3 -> 0 dropped.
    const out = scoreProbabilistic(pinRows, mk, em);
    expect(out).toHaveLength(1);
    expect(out[0]?.idA).toBe(0);
    expect(out[0]?.idB).toBe(1);
    expect(out[0]?.score).toBe(0.625);
    // Lowered threshold exposes the other two exact scores.
    const all = scoreProbabilistic(pinRows, mk, em, { threshold: 0 });
    const scores = all
      .map((p) => [p.idA, p.idB, p.score] as const)
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    expect(scores).toEqual([
      [0, 1, 0.625],
      [0, 2, 0.375],
      [1, 2, 0],
    ]);
  });

  it("scoreProbabilisticPair pins", () => {
    expect(scoreProbabilisticPair(pinRows[0]!, pinRows[1]!, mk, em)).toBe(0.625);
    expect(scoreProbabilisticPair(pinRows[0]!, pinRows[2]!, mk, em)).toBe(0.375);
    expect(scoreProbabilisticPair(pinRows[1]!, pinRows[2]!, mk, em)).toBe(0);
  });
});

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
