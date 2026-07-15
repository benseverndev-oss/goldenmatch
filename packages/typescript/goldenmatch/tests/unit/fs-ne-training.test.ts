/**
 * FS negative evidence — trainEM suites (T5), split out of
 * fs-negative-evidence.test.ts at T6 when the homonym E2E pushed that file
 * past the ~45-test split point.
 *
 * Covers: trainEM learning NE dims (separate NE matrix, u from the same
 * random-pair sample, full-likelihood E-step, m-only M-step, STORAGE-ONLY
 * clamp), the exact-replication clamp probe, and the no-NE training
 * regression pin. Loader/scoring/validation/fallback NE behavior stays in
 * fs-negative-evidence.test.ts.
 */
import { describe, it, expect } from "vitest";
import {
  trainEM,
  validateEmResultFor,
  neFired,
  buildComparisonVector,
} from "../../src/core/probabilistic.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type NegativeEvidenceField,
  type MatchkeyConfig,
  type Row,
} from "../../src/core/types.js";

// ---------------------------------------------------------------------------
// Fixtures (shared with fs-negative-evidence.test.ts by shape, not import —
// each file stays self-contained under pytest-xdist-style per-file runs).
// ---------------------------------------------------------------------------

const tinyRows: Row[] = [
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

// ---------------------------------------------------------------------------
// T5: trainEM learns NE dims (mirrors Python train_em's NE integration:
// separate NE matrix, u from the same random-pair sample, full-likelihood
// E-step, m-only M-step, STORAGE-ONLY clamp).
// ---------------------------------------------------------------------------

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
    const tiny = trainEM(tinyRows, neProbMatchkey());
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
  // No blockingFields handling anywhere in this replication: the probe
  // fixture trains without blocking fields, so production's neutral-u /
  // fixed-weight branches never activate.
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

      // Convergence: max m delta INCLUDING NE dims (mirrors the convergence
      // sweep at the bottom of Python `train_em`'s EM loop).
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
