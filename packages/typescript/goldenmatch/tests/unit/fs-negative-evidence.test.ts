/**
 * FS negative evidence (TS port phase 3).
 *
 * T1: loud decline — entry points that cannot honor NE throw
 * `NegativeEvidenceUnsupportedError` on a probabilistic matchkey carrying
 * non-empty `negativeEvidence`, killing the silent-wrong-scores state where
 * a Python-authored NE config scored in TS without the veto. After T5 the
 * whole discrete path (training + scoring + validation + fallback) honors
 * NE; only the continuous (Winkler) path throws PERMANENTLY, matching
 * Python.
 *
 * T3: `neFired` + `fsWeightRange`. T4: scoring + validation + fallback.
 * T5: `trainEM` learns NE dims (separate NE matrix, storage-only clamp).
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
  fallbackResult,
  buildComparisonVector,
  emResultToJson,
  emResultFromJson,
  FSModelMismatchError,
} from "../../src/core/probabilistic.js";
import type { EMResult, ContinuousEMResult } from "../../src/core/probabilistic.js";
import { parseConfig } from "../../src/core/config/loader.js";
import { findFuzzyMatches } from "../../src/core/scorer.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type NegativeEvidenceField,
  type MatchkeyConfig,
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

// The guards must fire BEFORE the model is touched, so a dummy suffices.
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
// Remaining declines: the continuous (Winkler) path only — PERMANENT.
// The whole discrete path (trainEM included, T5) honors NE; its behavior is
// pinned in the T4/T5 suites below.
// ---------------------------------------------------------------------------

describe("FS negative-evidence loud decline (probabilistic matchkeys)", () => {
  it("trainEM does NOT throw on NE configs (T5 lifted the decline)", () => {
    // Tiny dataset -> fallback EMResult, which is NE-complete (T4).
    const em = trainEM(rows, neProbMatchkey());
    expect(em.matchWeights["__ne__phone"]).toEqual([-3.0, 0.0]);
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
// probabilistic+NE config must reach the PERMANENT continuous-path throw,
// not silently score. (Repointed from trainEM at T5 when its throw lifted —
// the loader-composition property itself is what this pins.)
// ---------------------------------------------------------------------------

describe("loader-parsed NE config hits the decline", () => {
  it("parseConfig -> trainEMContinuous throws", () => {
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
    expectNeDecline(() => trainEMContinuous(rows, mk!));
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

// ---------------------------------------------------------------------------
// T4: FS scoring with negative evidence (mirrors Python
// `_ne_scalar_contribution` inside `score_probabilistic`).
// ---------------------------------------------------------------------------

describe("FS scoring with negative evidence", () => {
  // name exact [-1, 2]; NE phone exact, threshold 1.0, EM-learned [-3, 0].
  // Envelope: min = -1 + -3 = -4, max = 2 + 0 = 2, range 6.
  const mkNE = makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
    negativeEvidence: [
      makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 1.0 }),
    ],
  });
  const mkNoNE = makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
  });
  const em = makeEm({ name: [-1, 2], __ne__phone: [-3, 0] });
  const emNoNE = makeEm({ name: [-1, 2] });

  const sRows: Row[] = [
    { __row_id__: 1, name: "a", phone: "111" } as Row,
    { __row_id__: 2, name: "a", phone: "222" } as Row, // NE fires vs row 1
    { __row_id__: 3, name: "b", phone: "333" } as Row,
    { __row_id__: 4, name: "b", phone: "333" } as Row, // NE unfired (agree)
    { __row_id__: 5, name: "c", phone: "444" } as Row,
    { __row_id__: 6, name: "c", phone: null } as Row, // NE inconclusive (null)
  ];

  function scoreMap(
    rws: readonly Row[],
    mk: ReturnType<typeof makeMatchkeyConfig>,
    emr: EMResult,
  ): Map<string, number> {
    const out = scoreProbabilistic(rws, mk, emr, { threshold: 0 });
    return new Map(out.map((p) => [`${p.idA}:${p.idB}`, p.score]));
  }

  it("fired pair drops by exactly wFired pre-normalization (hand-computed normalized)", () => {
    const scores = scoreMap(sRows, mkNE, em);
    // (1,2): name agrees (+2), NE fires (-3) -> total -1 -> (-1 + 4)/6 = 0.5.
    expect(scores.get("1:2")).toBe(0.5);
    // Same regular total with NE unfired: (2 + 4)/6 = 1.0. The gap is
    // exactly wFired/range = 3/6.
    expect(scores.get("3:4")).toBe(1);
  });

  it("unfired pair scores identically to the same config without NE (both run)", () => {
    const withNe = scoreProbabilisticPair(sRows[2]!, sRows[3]!, mkNE, em);
    const withoutNe = scoreProbabilisticPair(sRows[2]!, sRows[3]!, mkNoNE, emNoNE);
    expect(withNe).toBe(withoutNe);
    expect(withNe).toBe(1);
  });

  it("null on one side is inconclusive: NE does not fire", () => {
    expect(scoreProbabilisticPair(sRows[4]!, sRows[5]!, mkNE, em)).toBe(1);
  });

  it("penaltyBits override is honored as -abs (no __ne__ entry needed)", () => {
    const mkBits = (bits: number) =>
      makeMatchkeyConfig({
        name: "p",
        type: "probabilistic",
        fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
        negativeEvidence: [
          makeNegativeEvidenceField({
            field: "phone",
            scorer: "exact",
            threshold: 1.0,
            penaltyBits: bits,
          }),
        ],
      });
    // Envelope: min = -1 - 5 = -6, max = 2, range 8. Fired: 2 - 5 = -3 -> 3/8.
    expect(scoreProbabilisticPair(sRows[0]!, sRows[1]!, mkBits(5), emNoNE)).toBe(3 / 8);
    // Negative bits take abs -- same score.
    expect(scoreProbabilisticPair(sRows[0]!, sRows[1]!, mkBits(-5), emNoNE)).toBe(3 / 8);
  });

  it("normalized stays in [0,1] when NE fires (fsWeightRange envelope)", () => {
    // All-min pair: name disagrees (-1) + NE fires (-3) = -4 = envelope min.
    const worst = scoreProbabilisticPair(
      { name: "x", phone: "1" } as Row,
      { name: "y", phone: "2" } as Row,
      mkNE,
      em,
    );
    expect(worst).toBe(0);
    for (const s of scoreMap(sRows, mkNE, em).values()) {
      expect(s).toBeGreaterThanOrEqual(0);
      expect(s).toBeLessThanOrEqual(1);
    }
  });

  it("round-4 applies to scoreProbabilistic ONLY; Pair returns raw floats", () => {
    // __ne__phone [-4, 0]: fired total = 2 - 4 = -2; envelope [-5, 2],
    // range 7 -> (-2 + 5)/7 = 3/7 = 0.42857142857...
    const em47 = makeEm({ name: [-1, 2], __ne__phone: [-4, 0] });
    expect(scoreProbabilisticPair(sRows[0]!, sRows[1]!, mkNE, em47)).toBe(3 / 7);
    expect(scoreMap(sRows, mkNE, em47).get("1:2")).toBe(0.4286);
  });

  it("fired NE with a missing __ne__ entry throws loudly (post-validate programming error)", () => {
    // Mirrors Python's KeyError contract in `_ne_scalar_contribution`:
    // never silently contribute 0 once the field fires.
    const badEm = makeEm({ name: [-1, 2] }); // no __ne__phone, no penaltyBits
    expect(() =>
      scoreProbabilisticPair(sRows[0]!, sRows[1]!, mkNE, badEm),
    ).toThrow(FSModelMismatchError);
  });
});

// ---------------------------------------------------------------------------
// T4: validateEmResultFor NE entry checks (mirrors Python `validate_for`).
// ---------------------------------------------------------------------------

describe("validateEmResultFor with negative evidence", () => {
  function neMk(extra?: { penaltyBits?: number }) {
    return makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
      negativeEvidence: [
        makeNegativeEvidenceField({
          field: "phone",
          scorer: "exact",
          threshold: 0.5,
          ...(extra?.penaltyBits !== undefined
            ? { penaltyBits: extra.penaltyBits }
            : {}),
        }),
      ],
    });
  }

  it("missing __ne__<field> entry: error names the field AND both remedies", () => {
    const em = makeEm({ name: [-1, 2] });
    let thrown: unknown;
    try {
      validateEmResultFor(em, neMk());
    } catch (err) {
      thrown = err;
    }
    expect(thrown).toBeInstanceOf(FSModelMismatchError);
    const msg = (thrown as Error).message;
    expect(msg).toContain("phone");
    expect(msg).toContain("__ne__phone");
    expect(msg).toContain("retrain");
    expect(msg).toContain("penaltyBits");
  });

  it("penaltyBits NE field requires NO __ne__ entry", () => {
    const em = makeEm({ name: [-1, 2] });
    expect(() => validateEmResultFor(em, neMk({ penaltyBits: 3 }))).not.toThrow();
  });

  it("1-entry __ne__ list rejected (2-element [fired, not_fired] required)", () => {
    const em = makeEm({ name: [-1, 2], __ne__phone: [-3] });
    expect(() => validateEmResultFor(em, neMk())).toThrow(/2-element/);
  });

  it("valid NE model passes", () => {
    const em = makeEm({ name: [-1, 2], __ne__phone: [-3, 0] });
    expect(() => validateEmResultFor(em, neMk())).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// T4: fallbackResult NE entries (mirrors Python `_fallback_result`).
// ---------------------------------------------------------------------------

describe("fallbackResult with negative evidence", () => {
  it("penaltyBits-free NE fields get the Python fallback entries; penaltyBits fields get none", () => {
    const mk = makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
      negativeEvidence: [
        makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 0.5 }),
        makeNegativeEvidenceField({
          field: "fax",
          scorer: "exact",
          threshold: 0.5,
          penaltyBits: 2,
        }),
      ],
    });
    const fb = fallbackResult(mk);
    // m=0.0625, u=0.5 -> log2(0.0625/0.5) == -3.0 exactly.
    expect(fb.matchWeights["__ne__phone"]).toEqual([-3.0, 0.0]);
    expect(fb.m["__ne__phone"]).toEqual([0.0625, 0.9375]);
    expect(fb.u["__ne__phone"]).toEqual([0.5, 0.5]);
    // penaltyBits NE fields: NO entries (fixed override).
    expect(fb.matchWeights["__ne__fax"]).toBeUndefined();
    expect(fb.m["__ne__fax"]).toBeUndefined();
    expect(fb.u["__ne__fax"]).toBeUndefined();
    // Self-consistency: the fallback validates against its own matchkey.
    expect(() => validateEmResultFor(fb, mk)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// T4 serde pin: __ne__ keys ride the generic dict passthrough untouched.
// ---------------------------------------------------------------------------

describe("EMResult serde round-trips __ne__ keys", () => {
  it("emResultToJson -> emResultFromJson preserves __ne__ entries exactly", () => {
    const em: EMResult = {
      m: { name: [0.1, 0.9], __ne__phone: [0.0625, 0.9375] },
      u: { name: [0.9, 0.1], __ne__phone: [0.5, 0.5] },
      matchWeights: {
        name: [-3.169925001442312, 3.169925001442312],
        __ne__phone: [-3.0, 0.0],
      },
      proportionMatched: 0.05,
      iterations: 3,
      converged: true,
      tfFreqs: null,
      tfCollision: null,
    };
    const json = emResultToJson(em);
    // The snake_case top-level keys (m_probs/u_probs/match_weights) carry
    // the __ne__ inner keys untouched on the JSON intermediate.
    expect((json["m_probs"] as Record<string, unknown>)["__ne__phone"]).toEqual([
      0.0625, 0.9375,
    ]);
    expect((json["u_probs"] as Record<string, unknown>)["__ne__phone"]).toEqual([
      0.5, 0.5,
    ]);
    expect(
      (json["match_weights"] as Record<string, unknown>)["__ne__phone"],
    ).toEqual([-3.0, 0.0]);
    const back = emResultFromJson(json);
    expect(back).toEqual(em);
    // Byte-identical re-serialization.
    expect(emResultToJson(back)).toEqual(json);
  });
});

// ---------------------------------------------------------------------------
// T5: trainEM learns NE dims (mirrors Python train_em's NE integration:
// separate NE matrix, u from the same random-pair sample, full-likelihood
// E-step, m-only M-step, STORAGE-ONLY clamp).
// ---------------------------------------------------------------------------

// 80 rows: 40 duplicate pairs. Matched pairs share the NE value (phone), so
// NE never fires within a true match; every cross pair disagrees, so NE
// fires whenever both sides are present. <= 100 rows keeps samplePairs on
// its deterministic enumerate-all-pairs branch (80*79/2 = 3160 <= 5000).
const trainRows: Row[] = Array.from({ length: 80 }, (_, i) => ({
  __row_id__: i,
  name: `person${Math.floor(i / 2)}`,
  phone: `555-${1000 + Math.floor(i / 2)}`,
})) as Row[];

function trainNeMk(extraNe?: NegativeEvidenceField[]): MatchkeyConfig {
  return makeMatchkeyConfig({
    name: "p",
    type: "probabilistic",
    fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
    negativeEvidence: [
      makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 0.5 }),
      ...(extraNe ?? []),
    ],
  });
}

describe("trainEM learns NE dims", () => {
  const em = trainEM(trainRows, trainNeMk());

  it("m/u __ne__ entries are 2-lists summing to 1", () => {
    for (const table of [em.m, em.u]) {
      const entry = table["__ne__phone"];
      expect(entry).toHaveLength(2);
      expect(entry![0]! + entry![1]!).toBeCloseTo(1, 9);
    }
  });

  it("matchWeights __ne__ entry is [negative wFired, exactly 0.0]", () => {
    const w = em.matchWeights["__ne__phone"];
    expect(w).toHaveLength(2);
    expect(w![0]!).toBeLessThan(0);
    expect(w![1]).toBe(0.0);
  });

  it("validateEmResultFor passes on the trained result (T4 integration)", () => {
    expect(() => validateEmResultFor(em, trainNeMk())).not.toThrow();
  });

  it("converged/iterations fields still populated", () => {
    expect(em.iterations).toBeGreaterThanOrEqual(1);
    expect(typeof em.converged).toBe("boolean");
  });

  it("penaltyBits NE fields produce NO __ne__ entries anywhere", () => {
    const mk = trainNeMk([
      makeNegativeEvidenceField({
        field: "fax",
        scorer: "exact",
        threshold: 0.5,
        penaltyBits: 2,
      }),
    ]);
    const trained = trainEM(trainRows, mk);
    expect(trained.m["__ne__fax"]).toBeUndefined();
    expect(trained.u["__ne__fax"]).toBeUndefined();
    expect(trained.matchWeights["__ne__fax"]).toBeUndefined();
    // The penaltyBits-free sibling still trains.
    expect(trained.matchWeights["__ne__phone"]).toHaveLength(2);
    expect(() => validateEmResultFor(trained, mk)).not.toThrow();
  });

  it("all-null NE column: never fires, u[fired] lands on the smoothing floor", () => {
    const nullRows: Row[] = Array.from({ length: 20 }, (_, i) => ({
      __row_id__: i,
      name: `person${Math.floor(i / 2)}`,
      phone: null,
    })) as Row[];
    const trained = trainEM(nullRows, trainNeMk());
    const u = trained.u["__ne__phone"]!;
    // 190 pairs, 0 fired: u0 = 1e-6 / (190 + 2e-6) ~ 5.26e-9.
    expect(u[0]!).toBeGreaterThan(0);
    expect(u[0]!).toBeLessThan(1e-8);
    expect(u[0]! + u[1]!).toBeCloseTo(1, 9);
    expect(trained.matchWeights["__ne__phone"]![1]).toBe(0.0);
  });

  it("early-return fallbacks are NE-complete (tiny dataset + zero fields)", () => {
    // < 10 pairs -> fallbackResult.
    const tiny = trainEM(rows, neProbMatchkey());
    expect(tiny.matchWeights["__ne__phone"]).toEqual([-3.0, 0.0]);
    expect(() => validateEmResultFor(tiny, neProbMatchkey())).not.toThrow();
    // fields.length === 0 -> fallbackResult.
    const noFields = makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [],
      negativeEvidence: [
        makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 0.5 }),
      ],
    });
    const fb = trainEM(trainRows, noFields);
    expect(fb.matchWeights["__ne__phone"]).toEqual([-3.0, 0.0]);
  });
});

// ---------------------------------------------------------------------------
// T5: storage-only clamp probe — hand-replicate the EM loop in plain JS with
// FULL NE likelihood in the E-step and compare against production to 1e-9.
// If production leaked the [w, 0] clamp into training, the regular-field m
// AND the NE m would both diverge from this replication. (Python pinned the
// same subtlety with exact probes.)
// ---------------------------------------------------------------------------

describe("storage-only clamp probe (2-iteration exact replication)", () => {
  function replicate(rowsIn: readonly Row[], mk: MatchkeyConfig, maxIterations: number) {
    // samplePairs enumerates ALL pairs deterministically (i < j, row order)
    // when total pairs <= min(nSamplePairs, 5000) — verified property.
    const ids: number[] = [];
    for (const r of rowsIn) {
      const id = r["__row_id__"];
      if (typeof id === "number") ids.push(id);
    }
    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) pairs.push([ids[i]!, ids[j]!]);
    }
    const rowById = new Map<number, Row>();
    for (const r of rowsIn) rowById.set(r["__row_id__"] as number, r);

    const fields = mk.fields;
    const comp = pairs.map(([a, b]) =>
      buildComparisonVector(rowById.get(a)!, rowById.get(b)!, fields),
    );
    const neFieldsEm = (mk.negativeEvidence ?? []).filter(
      (ne) => ne.penaltyBits === undefined,
    );
    // SEPARATE NE matrix: 0 = fired, 1 = not-fired (incl. nulls/empties).
    const neMat = pairs.map(([a, b]) =>
      neFieldsEm.map((ne) => (neFired(rowById.get(a)!, rowById.get(b)!, ne) ? 0 : 1)),
    );

    // u (regular): observed level rates + 1e-6 smoothing.
    const u: Record<string, number[]> = {};
    fields.forEach((f, j) => {
      const n = f.levels ?? 2;
      const counts = new Array<number>(n).fill(0);
      for (const row of comp) {
        const lvl = row[j]!;
        if (lvl >= 0 && lvl < n) counts[lvl]! += 1;
      }
      const total = counts.reduce((a, b) => a + b, 0) + n * 1e-6;
      u[f.field] = counts.map((c) => (c + 1e-6) / total);
    });
    // u (NE): same smoothing idiom over the NE matrix.
    const uNe: Record<string, number[]> = {};
    neFieldsEm.forEach((ne, j) => {
      let fired = 0;
      let notFired = 0;
      for (const row of neMat) {
        if (row[j] === 0) fired += 1;
        else notFired += 1;
      }
      const total = fired + notFired + 2 * 1e-6;
      uNe[ne.field] = [(fired + 1e-6) / total, (notFired + 1e-6) / total];
    });

    // m priors: exponential (regular); [0.05, 0.95] (NE).
    const m: Record<string, number[]> = {};
    for (const f of fields) {
      const n = f.levels ?? 2;
      const raw: number[] = [];
      for (let k = 0; k < n; k++) raw.push(2 ** k);
      const s = raw.reduce((a, b) => a + b, 0);
      m[f.field] = raw.map((r) => r / s);
    }
    const mNe: Record<string, number[]> = {};
    for (const ne of neFieldsEm) mNe[ne.field] = [0.05, 0.95];

    let pMatch = 0.02;
    const nPairs = comp.length;
    for (let iter = 0; iter < maxIterations; iter++) {
      const oldM: Record<string, number[]> = {};
      for (const k of Object.keys(m)) oldM[k] = [...m[k]!];
      const oldMNe: Record<string, number[]> = {};
      for (const k of Object.keys(mNe)) oldMNe[k] = [...mNe[k]!];

      // E-step: FULL likelihood — for a not-fired NE event the model term is
      // log(m1)/log(u1), never a zeroed weight.
      const posteriors = new Float64Array(nPairs);
      for (let i = 0; i < nPairs; i++) {
        let logM = Math.log(Math.max(pMatch, 1e-10));
        let logU = Math.log(Math.max(1 - pMatch, 1e-10));
        for (let j = 0; j < fields.length; j++) {
          const f = fields[j]!;
          const level = comp[i]![j]!;
          logM += Math.log(Math.max(m[f.field]![level] ?? 1e-10, 1e-10));
          logU += Math.log(Math.max(u[f.field]![level] ?? 1e-10, 1e-10));
        }
        for (let j = 0; j < neFieldsEm.length; j++) {
          const ne = neFieldsEm[j]!;
          const ev = neMat[i]![j]!;
          logM += Math.log(Math.max(mNe[ne.field]![ev]!, 1e-10));
          logU += Math.log(Math.max(uNe[ne.field]![ev]!, 1e-10));
        }
        const maxLog = Math.max(logM, logU);
        const eM = Math.exp(logM - maxLog);
        const eU = Math.exp(logU - maxLog);
        posteriors[i] = eM / (eM + eU);
      }

      // M-step (m only).
      let totalMatch = 0;
      for (let i = 0; i < nPairs; i++) totalMatch += posteriors[i]!;
      pMatch = Math.max(totalMatch / nPairs, 1e-6);

      for (let j = 0; j < fields.length; j++) {
        const f = fields[j]!;
        const n = f.levels ?? 2;
        const newM = new Array<number>(n).fill(0);
        for (let i = 0; i < nPairs; i++) {
          const level = comp[i]![j]!;
          if (level >= 0 && level < n) newM[level]! += posteriors[i]!;
        }
        const denom = totalMatch + n * 1e-6;
        for (let k = 0; k < n; k++) newM[k] = (newM[k]! + 1e-6) / denom;
        m[f.field] = newM;
      }
      for (let j = 0; j < neFieldsEm.length; j++) {
        const ne = neFieldsEm[j]!;
        const newM = [0, 0];
        for (let i = 0; i < nPairs; i++) newM[neMat[i]![j]!]! += posteriors[i]!;
        const denom = totalMatch + 2 * 1e-6;
        mNe[ne.field] = [(newM[0]! + 1e-6) / denom, (newM[1]! + 1e-6) / denom];
      }

      // Convergence: max m delta INCLUDING NE dims (Python lines ~807-815).
      let maxDelta = 0;
      for (const f of fields) {
        const n = f.levels ?? 2;
        for (let k = 0; k < n; k++) {
          const d = Math.abs(m[f.field]![k]! - oldM[f.field]![k]!);
          if (d > maxDelta) maxDelta = d;
        }
      }
      for (const ne of neFieldsEm) {
        for (let k = 0; k < 2; k++) {
          const d = Math.abs(mNe[ne.field]![k]! - oldMNe[ne.field]![k]!);
          if (d > maxDelta) maxDelta = d;
        }
      }
      if (maxDelta < 0.001) break;
    }
    return { m, mNe, u, uNe };
  }

  it("production trainEM(maxIterations: 2) matches the full-likelihood replication to 1e-9", () => {
    const mk = trainNeMk();
    const prod = trainEM(trainRows, mk, { maxIterations: 2 });
    const rep = replicate(trainRows, mk, 2);

    // Regular-field m: biased if the clamp leaked into the E-step.
    for (let k = 0; k < 2; k++) {
      expect(Math.abs(prod.m["name"]![k]! - rep.m["name"]![k]!)).toBeLessThan(1e-9);
      expect(Math.abs(prod.u["name"]![k]! - rep.u["name"]![k]!)).toBeLessThan(1e-9);
    }
    // NE m/u.
    for (let k = 0; k < 2; k++) {
      expect(Math.abs(prod.m["__ne__phone"]![k]! - rep.mNe["phone"]![k]!)).toBeLessThan(1e-9);
      expect(Math.abs(prod.u["__ne__phone"]![k]! - rep.uNe["phone"]![k]!)).toBeLessThan(1e-9);
    }
    // Storage: [log2(m0/u0), 0.0] with the epsilon guards — clamp applied
    // ONLY here, never inside the loop above.
    const m0 = Math.max(rep.mNe["phone"]![0]!, 1e-10);
    const u0 = Math.max(rep.uNe["phone"]![0]!, 1e-10);
    expect(Math.abs(prod.matchWeights["__ne__phone"]![0]! - Math.log2(m0 / u0))).toBeLessThan(1e-9);
    expect(prod.matchWeights["__ne__phone"]![1]).toBe(0.0);
  });
});

// ---------------------------------------------------------------------------
// T5 regression pin: no-NE configs train byte-identically after the NE
// integration (values produced by the PRE-T5 trainEM on this fixture).
// ---------------------------------------------------------------------------

describe("no-NE trainEM regression pin", () => {
  it("trained result is unchanged by the NE integration", () => {
    const pinRows12: Row[] = Array.from({ length: 12 }, (_, i) => ({
      __row_id__: i,
      name: `n${i % 4}`,
      city: `c${i % 3}`,
    })) as Row[];
    const mk = makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [
        makeMatchkeyField({ field: "name", scorer: "exact" }),
        makeMatchkeyField({ field: "city", scorer: "exact" }),
      ],
    });
    const em = trainEM(pinRows12, mk);
    expect(em.m).toEqual({
      name: [0.05777959651033018, 0.9422204034896711],
      city: [0.9999997034526049, 2.965473953890052e-7],
    });
    expect(em.u).toEqual({
      name: [0.8181818085399453, 0.18181819146005482],
      city: [0.7272727203856751, 0.2727272796143249],
    });
    expect(em.matchWeights).toEqual({
      name: [-3.8237894263482755, 2.3735680206997056],
      city: [0.4594312044716621, -19.810764882245056],
    });
    expect(em.proportionMatched).toBe(0.05556349258411293);
    expect(em.iterations).toBe(20);
    expect(em.converged).toBe(false);
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
