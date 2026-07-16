import { describe, it, expect } from "vitest";
import {
  emResultToJson,
  emResultFromJson,
  validateEmResultFor,
  FSModelMismatchError,
  type EMResult,
  makeMatchkeyConfig,
  makeMatchkeyField,
} from "../../src/core/index.js";

// ---------------------------------------------------------------------------
// Round-trip TS -> JSON -> TS
// ---------------------------------------------------------------------------

describe("emResultToJson / emResultFromJson: round-trip", () => {
  it("round-trips without tf fields", () => {
    const em: EMResult = {
      m: { first_name: [0.05, 0.1, 0.25, 0.6], postcode: [0.1, 0.9] },
      u: { first_name: [0.85, 0.1, 0.04, 0.01], postcode: [0.9, 0.1] },
      matchWeights: {
        first_name: [-4.09, 0.0, 2.64, 5.91],
        postcode: [-3.17, 3.17],
      },
      converged: true,
      iterations: 12,
      proportionMatched: 0.0002,
    };
    const json = emResultToJson(em);
    expect(json).toMatchObject({
      __type__: "goldenmatch.EMResult",
      __version__: 2,
      m_probs: em.m,
      u_probs: em.u,
      match_weights: em.matchWeights,
      converged: true,
      iterations: 12,
      proportion_matched: 0.0002,
      tf_freqs: null,
      tf_collision: null,
    });

    const back = emResultFromJson(json);
    expect(back.m).toEqual(em.m);
    expect(back.u).toEqual(em.u);
    expect(back.matchWeights).toEqual(em.matchWeights);
    expect(back.converged).toBe(em.converged);
    expect(back.iterations).toBe(em.iterations);
    expect(back.proportionMatched).toBe(em.proportionMatched);
    expect(back.tfFreqs ?? null).toBeNull();
    expect(back.tfCollision ?? null).toBeNull();
  });

  it("round-trips with tf fields present", () => {
    const em: EMResult = {
      m: { city: [0.1, 0.9] },
      u: { city: [0.7, 0.3] },
      matchWeights: { city: [-2.8, 1.58] },
      converged: false,
      iterations: 5,
      proportionMatched: 0.01,
      tfFreqs: { city: { springfield: 0.02, columbus: 0.015 } },
      tfCollision: { city: 0.0009 },
    };
    const json = emResultToJson(em);
    const back = emResultFromJson(json);
    expect(back.tfFreqs).toEqual(em.tfFreqs);
    expect(back.tfCollision).toEqual(em.tfCollision);

    // Re-serializing must be byte-identical (JSON.stringify equal) — a
    // Python-trained model's tf tables must survive a TS re-save untouched.
    const rejson = emResultToJson(back);
    expect(JSON.stringify(rejson)).toBe(JSON.stringify(json));
  });
});

// ---------------------------------------------------------------------------
// Cross-surface fixture: exact shape Python's EMResult.to_dict()/save_json
// produces (goldenmatch/core/probabilistic.py, SCHEMA_VERSION=2).
// ---------------------------------------------------------------------------

describe("emResultFromJson: cross-surface Python fixture", () => {
  const pythonFixture = {
    __type__: "goldenmatch.EMResult",
    __version__: 2,
    m_probs: {
      first_name: [0.05, 0.1, 0.25, 0.6],
      postcode: [0.1, 0.9],
    },
    u_probs: {
      first_name: [0.85, 0.1, 0.04, 0.01],
      postcode: [0.9, 0.1],
    },
    match_weights: {
      first_name: [-4.09, 0.0, 2.64, 5.91],
      postcode: [-3.17, 3.17],
    },
    converged: true,
    iterations: 0,
    proportion_matched: 0.0002,
    tf_freqs: null,
    tf_collision: null,
  };

  it("loads a Python-produced JSON blob and lands fields correctly", () => {
    const em = emResultFromJson(pythonFixture);
    expect(em.m["first_name"]).toEqual([0.05, 0.1, 0.25, 0.6]);
    expect(em.u["postcode"]).toEqual([0.9, 0.1]);
    expect(em.matchWeights["first_name"]).toEqual([-4.09, 0.0, 2.64, 5.91]);
    expect(em.converged).toBe(true);
    expect(em.iterations).toBe(0);
    expect(em.proportionMatched).toBe(0.0002);
    expect(em.tfFreqs ?? null).toBeNull();
    expect(em.tfCollision ?? null).toBeNull();
  });

  it("re-serializes to the same wire shape", () => {
    const em = emResultFromJson(pythonFixture);
    const json = emResultToJson(em);
    expect(json).toEqual(pythonFixture);
  });
});

// ---------------------------------------------------------------------------
// Version / missing-key rejection
// ---------------------------------------------------------------------------

describe("emResultFromJson: forward-compat + validation errors", () => {
  it("rejects a schema version newer than supported", () => {
    const data = {
      __type__: "goldenmatch.EMResult",
      __version__: 999,
      m_probs: {},
      u_probs: {},
      match_weights: {},
      converged: true,
      iterations: 0,
      proportion_matched: 0.0,
    };
    expect(() => emResultFromJson(data)).toThrow(
      /schema version 999 is newer than this goldenmatch supports \(2\)/,
    );
  });

  it("rejects v1 models trained with legacy missing-value semantics", () => {
    expect(() => emResultFromJson({
      __version__: 1,
      m_probs: {}, u_probs: {}, match_weights: {},
      converged: true, iterations: 1, proportion_matched: 0.1,
    })).toThrow(/legacy missing-value semantics/);
  });

  it("rejects a dict missing a required key", () => {
    const data = {
      __version__: 2,
      m_probs: {},
      u_probs: {},
      match_weights: {},
      converged: true,
      // iterations missing
      proportion_matched: 0.0,
    };
    expect(() => emResultFromJson(data)).toThrow(/missing required key: 'iterations'/);
  });
});

// ---------------------------------------------------------------------------
// validateEmResultFor
// ---------------------------------------------------------------------------

describe("validateEmResultFor", () => {
  it("passes when a 4-level levelThresholds field matches", () => {
    const mk = makeMatchkeyConfig({
      name: "mk",
      type: "probabilistic",
      fields: [
        makeMatchkeyField({
          field: "first_name",
          scorer: "jaro_winkler",
          levels: 4,
          levelThresholds: [1.0, 0.92, 0.88],
        }),
      ],
    });
    const em: EMResult = {
      m: { first_name: [0.05, 0.1, 0.25, 0.6] },
      u: { first_name: [0.85, 0.1, 0.04, 0.01] },
      matchWeights: { first_name: [-4.09, 0.0, 2.64, 5.91] },
      converged: true,
      iterations: 3,
      proportionMatched: 0.01,
    };
    expect(() => validateEmResultFor(em, mk)).not.toThrow();
  });

  it("throws when the level count mismatches", () => {
    const mk = makeMatchkeyConfig({
      name: "mk",
      type: "probabilistic",
      fields: [
        makeMatchkeyField({
          field: "first_name",
          scorer: "jaro_winkler",
          levels: 4,
          levelThresholds: [1.0, 0.92, 0.88],
        }),
      ],
    });
    const em: EMResult = {
      m: { first_name: [0.05, 0.95] },
      u: { first_name: [0.85, 0.15] },
      matchWeights: { first_name: [-4.09, 5.91] },
      converged: true,
      iterations: 3,
      proportionMatched: 0.01,
    };
    expect(() => validateEmResultFor(em, mk)).toThrow(FSModelMismatchError);
    expect(() => validateEmResultFor(em, mk)).toThrow(
      /has 2 levels but the matchkey expects 4/,
    );
  });

  it("throws when a field has no weights at all", () => {
    const mk = makeMatchkeyConfig({
      name: "mk",
      type: "probabilistic",
      fields: [
        makeMatchkeyField({ field: "postcode", scorer: "exact", levels: 2 }),
      ],
    });
    const em: EMResult = {
      m: {},
      u: {},
      matchWeights: {},
      converged: true,
      iterations: 1,
      proportionMatched: 0.01,
    };
    expect(() => validateEmResultFor(em, mk)).toThrow(
      /Persisted FS model has no weights for field 'postcode'/,
    );
  });
});
