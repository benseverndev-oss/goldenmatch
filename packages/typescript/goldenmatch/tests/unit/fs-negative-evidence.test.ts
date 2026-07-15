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
 * T6: the homonym E2E (#1764 success bar). The T5 trainEM suites (NE dims,
 * clamp probe, no-NE training pin) live in fs-ne-training.test.ts — split
 * out at T6 when this file passed the ~45-test split point.
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
  emResultToJson,
  emResultFromJson,
  FSModelMismatchError,
  NegativeEvidenceUnsupportedError,
} from "../../src/core/probabilistic.js";
import {
  runDedupePipeline,
  runMatchPipeline,
  makeConfig,
  makeBlockingConfig,
} from "../../src/core/index.js";
import type { EMResult, ContinuousEMResult } from "../../src/core/probabilistic.js";
import { parseConfig } from "../../src/core/config/loader.js";
import { findFuzzyMatches } from "../../src/core/scorer.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type NegativeEvidenceField,
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
// T6: homonym E2E — the #1764 success bar in TS. Distinct people sharing
// name+city (homonym traps) merge under plain FS; wiring phone in as
// negative evidence (EM-learned AND penaltyBits) separates them while true
// duplicates keep merging.
//
// Fixture lessons carried over from Python tests/test_fs_ne_e2e.py, adapted
// to TS's random-pair training (trainEM has NO blocked-pairs path, so no
// blocking config here and mixed identities arrive via the random sample):
// - <= 100 rows keeps samplePairs on its deterministic enumerate-all-pairs
//   branch (80*79/2 = 3160 <= 5000) — no RNG-order flake.
// - given names / cities from real-word pools with NO reuse across the 40
//   identities: reused or synthetic near-identical pools (e.g. "given1" /
//   "given2", jaro_winkler ~0.93) inflate u and invert the fields'
//   discriminative power.
// - surnames spread across distinct soundex codes
//   (feedback_synthetic_surname_fixtures) — inert for scoring here (no
//   blocking), kept so the fixture stays valid if a blocked variant appears.
// - true dups: same city/phone, trailing single-char given-name corruption
//   that still clears the jaro_winkler 0.8 partial threshold.
// - LINK 0.98: without NE, dup AND trap pairs both normalize to 1.0 (no
//   threshold separates them — the failure mode); with NE the traps drop
//   below 0.98 while dups stay at 1.0 (phone agrees, NE never fires).
// ---------------------------------------------------------------------------

const E2E_LINK = 0.98;
const E2E_SURNAMES = [
  "Smith", "Johnson", "Garcia", "Nguyen", "Okafor",
  "Petrov", "Alvarez", "Kowalski", "Haddad", "Kim",
];
const E2E_GIVEN = [
  "Jonathan", "Michael", "Robert", "Susan", "Karen", "David", "Linda",
  "Steven", "Nancy", "Brian", "Patricia", "Kevin", "Laura", "Edward",
  "Diane", "Gregory", "Sandra", "Peter", "Carol", "Frank", "Deborah",
  "Raymond", "Cynthia", "Jack", "Amy", "Dennis", "Angela", "Jerry",
  "Melissa", "Tyler", "Brenda", "Aaron", "Emma", "Henry", "Julie", "Adam",
  "Joyce", "Douglas", "Virginia", "Nathan",
];
const E2E_CITIES = [
  "Boston", "Denver", "Austin", "Seattle", "Phoenix", "Atlanta", "Portland",
  "Chicago", "Miami", "Dallas", "Newark", "Tampa", "Reno", "Salem",
  "Fresno", "Toledo", "Akron", "Eugene", "Boise", "Tulsa", "Naples",
  "Modesto", "Waco", "Provo", "Yuma", "Erie", "Wichita", "Duluth", "Macon",
  "Biloxi", "Laredo", "Odessa", "Bangor", "Casper", "Sheridan", "Helena",
  "Butte", "Pierre", "Fargo", "Rapid",
];

function buildHomonymFixture(): {
  rows: Row[];
  dupPairs: Array<[number, number]>;
  trapPairs: Array<[number, number]>;
} {
  const rows: Row[] = [];
  const dupPairs: Array<[number, number]> = [];
  const trapPairs: Array<[number, number]> = [];
  let id = 0;
  let nameIdx = 0;
  for (const surname of E2E_SURNAMES) {
    for (let d = 0; d < 3; d++) {
      const givenName = E2E_GIVEN[nameIdx]!;
      const city = E2E_CITIES[nameIdx]!;
      nameIdx += 1;
      const phone = `555${1_000_000 + id}`;
      rows.push({ __row_id__: id, givenName, surname, city, phone } as Row);
      rows.push({
        __row_id__: id + 1,
        givenName: `${givenName}x`,
        surname,
        city,
        phone,
      } as Row);
      dupPairs.push([id, id + 1]);
      id += 2;
    }
    // Homonym trap: same given name + surname + city, DIFFERENT phone.
    const givenName = E2E_GIVEN[nameIdx]!;
    const city = E2E_CITIES[nameIdx]!;
    nameIdx += 1;
    rows.push({
      __row_id__: id,
      givenName,
      surname,
      city,
      phone: `555${2_000_000 + id}`,
    } as Row);
    rows.push({
      __row_id__: id + 1,
      givenName,
      surname,
      city,
      phone: `555${2_000_000 + id + 1}`,
    } as Row);
    trapPairs.push([id, id + 1]);
    id += 2;
  }
  return { rows, dupPairs, trapPairs };
}

function e2eMatchkey(ne?: NegativeEvidenceField) {
  return makeMatchkeyConfig({
    name: "fs",
    type: "probabilistic",
    fields: [
      makeMatchkeyField({
        field: "givenName",
        scorer: "jaro_winkler",
        levels: 2,
        partialThreshold: 0.8,
      }),
      makeMatchkeyField({ field: "city", scorer: "exact", levels: 2 }),
    ],
    linkThreshold: E2E_LINK,
    ...(ne !== undefined ? { negativeEvidence: [ne] } : {}),
  });
}

/** Pairs at/above the matchkey's linkThreshold, as "a:b" keys. */
function linkedPairs(rws: readonly Row[], mk: ReturnType<typeof e2eMatchkey>): Set<string> {
  const em = trainEM(rws, mk);
  return new Set(scoreProbabilistic(rws, mk, em).map((p) => `${p.idA}:${p.idB}`));
}

describe("homonym E2E: NE separates traps, true dups still merge (#1764 bar)", () => {
  const { rows: e2eRows, dupPairs, trapPairs } = buildHomonymFixture();

  it("PRECONDITION: without NE the homonym traps genuinely merge (the delta baseline)", () => {
    const linked = linkedPairs(e2eRows, e2eMatchkey());
    for (const [a, b] of dupPairs) {
      expect(linked.has(`${a}:${b}`), `true dup (${a}, ${b}) failed to link`).toBe(true);
    }
    for (const [a, b] of trapPairs) {
      expect(
        linked.has(`${a}:${b}`),
        `homonym trap (${a}, ${b}) did not link WITHOUT NE — fixture no longer ` +
          `demonstrates the failure this feature exists to fix`,
      ).toBe(true);
    }
  });

  for (const [variant, ne] of [
    [
      "EM-learned",
      makeNegativeEvidenceField({ field: "phone", scorer: "exact", threshold: 1.0 }),
    ],
    [
      "penaltyBits",
      makeNegativeEvidenceField({
        field: "phone",
        scorer: "exact",
        threshold: 1.0,
        penaltyBits: 6.0,
      }),
    ],
  ] as const) {
    it(`${variant} NE: traps separate, true dups still link`, () => {
      const linked = linkedPairs(e2eRows, e2eMatchkey(ne));
      for (const [a, b] of dupPairs) {
        expect(
          linked.has(`${a}:${b}`),
          `true dup (${a}, ${b}) STOPPED linking once NE was added — NE fired ` +
            `on a genuine duplicate (should never happen: phone agrees)`,
        ).toBe(true);
      }
      for (const [a, b] of trapPairs) {
        expect(
          linked.has(`${a}:${b}`),
          `homonym trap (${a}, ${b}) still linked with ${variant} NE wired in`,
        ).toBe(false);
      }
    });
  }
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

// ---------------------------------------------------------------------------
// PIPELINE decline: the full pipeline (dedupe/match) scores probabilistic
// matchkeys with simplified weighted-style averaging that cannot honor NE.
// It must throw NegativeEvidenceUnsupportedError BEFORE any scoring work —
// never silently score without the veto. Exact/weighted NE and NE-free
// probabilistic configs run in the pipeline unchanged.
// ---------------------------------------------------------------------------

describe("pipeline declines probabilistic+NE loudly", () => {
  const pipeRows: Row[] = [
    { id: 1, name: "Alice Smith", phone: "555-1111", zip: "111" },
    { id: 2, name: "Alice Smith", phone: "555-9999", zip: "111" },
    { id: 3, name: "Zeke Xavier", phone: "555-2222", zip: "222" },
  ];

  /** Loader-shaped probabilistic+NE config (the fan-out-lever YAML hazard). */
  const rawNeConfig = {
    matchkeys: [
      {
        name: "p",
        type: "probabilistic",
        fields: [
          { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 },
        ],
        negative_evidence: [
          { field: "phone", transforms: [], scorer: "exact", threshold: 0.5 },
        ],
      },
    ],
  };

  async function expectPipelineDecline(run: () => Promise<unknown>): Promise<void> {
    const err = await run().then(
      () => undefined,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(NegativeEvidenceUnsupportedError);
    const e = err as Error;
    expect(e.message).toContain("phone");
    expect(e.message).toContain("simplified");
    expect(e.message).toContain("trainEM");
  }

  it("loader-parsed probabilistic+NE config -> runDedupePipeline throws", async () => {
    const config = parseConfig(rawNeConfig);
    await expectPipelineDecline(() => runDedupePipeline(pipeRows, config));
  });

  it("loader-parsed probabilistic+NE config -> runMatchPipeline throws", async () => {
    const config = parseConfig(rawNeConfig);
    await expectPipelineDecline(() =>
      runMatchPipeline(pipeRows.slice(0, 1), pipeRows.slice(1), config),
    );
  });

  it("runMatchPipeline declines even when the target is empty (no silent early-return)", async () => {
    const config = parseConfig(rawNeConfig);
    await expectPipelineDecline(() => runMatchPipeline([], pipeRows, config));
  });

  it("weighted+NE config still runs through the pipeline (existing behavior untouched)", async () => {
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
    const blocking = makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: [] }],
    });
    const config = makeConfig({ matchkeys: [mk], blocking });
    const result = await runDedupePipeline(
      [
        { id: 1, name: "Alice Smith", phone: "555-1111", zip: "111" },
        { id: 2, name: "Alice Smith", phone: "555-1111", zip: "111" },
        { id: 3, name: "Alice Smith", phone: "555-9999", zip: "111" },
      ],
      config,
    );
    // Only the phone-agreeing pair survives the NE penalty.
    expect(result.scoredPairs.length).toBe(1);
    expect(result.scoredPairs[0]?.idA).toBe(0);
    expect(result.scoredPairs[0]?.idB).toBe(1);
  });

  it("probabilistic config WITHOUT NE runs through the pipeline unchanged", async () => {
    const mk = makeMatchkeyConfig({
      name: "p",
      type: "probabilistic",
      fields: [makeMatchkeyField({ field: "name", scorer: "jaro_winkler" })],
      threshold: 0.7,
    });
    const blocking = makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: [] }],
    });
    const config = makeConfig({ matchkeys: [mk], blocking });
    const result = await runDedupePipeline(pipeRows, config);
    expect(result.stats.totalRecords).toBe(3);
    // The two same-name rows still pair up on the simplified path.
    const hasMatch = result.scoredPairs.some(
      (p) =>
        (p.idA === 0 && p.idB === 1) || (p.idA === 1 && p.idB === 0),
    );
    expect(hasMatch).toBe(true);
  });
});
