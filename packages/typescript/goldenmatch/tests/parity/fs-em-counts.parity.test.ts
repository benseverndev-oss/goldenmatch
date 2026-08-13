/**
 * TS trains Fellegi-Sunter through the SHARED Rust kernel, and agrees with it.
 *
 * Phase 1b of docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md.
 * The EM loop exists three times in this repo (Python, this package's `trainEM`,
 * and Rust `em_core`). `trainFsFromCounts` is TS reaching the Rust one.
 *
 * The anchors are the SAME numbers
 * `packages/rust/extensions/score-core/tests/fixtures/em_counts_parity.json`
 * carries, emitted by `scripts/gen_fs_em_parity_fixture.py` from the Python
 * reference. Sharing anchors is what makes "one kernel" checkable: three
 * surfaces asserting against three sets of hand-copied numbers would agree with
 * their own copies and nothing else.
 *
 * Parity is decision-level, not bitwise -- libm's ln/log2/exp differ from
 * CPython's in the low mantissa bits.
 */
import { describe, expect, it } from "vitest";

import { trainFsFromCounts } from "../../src/core/fsWasm.js";
import type { FsTrainedModel } from "../../src/core/fsWasm.js";

/**
 * `table[j][k]`, failing loudly when the cell is absent.
 *
 * `noUncheckedIndexedAccess` is on, so a bare index is `number | undefined` --
 * and `expect(undefined).toBeCloseTo(x)` is exactly the kind of assertion that
 * looks like coverage while testing nothing. A model missing a whole field
 * should name the field, not surface as a type error at the assertion.
 */
function cell(table: readonly (readonly number[])[], j: number, k: number): number {
  const row = table[j];
  if (row === undefined) throw new Error(`no vector for field ${j}`);
  const v = row[k];
  if (v === undefined) throw new Error(`field ${j} has no level ${k}`);
  return v;
}

const U_TWO_FIELD: readonly (readonly number[])[] = [
  [0.9, 0.1],
  [0.85, 0.15],
];

function train(
  patterns: readonly { levels: readonly number[]; count: number }[],
  uProbs: readonly (readonly number[])[] = U_TWO_FIELD,
  conditioned?: readonly boolean[],
): FsTrainedModel {
  // `exactOptionalPropertyTypes` is on, so an explicit `conditioned: undefined`
  // is NOT the same as omitting the key -- spread it in only when supplied.
  return trainFsFromCounts({
    nLevels: [2, 2],
    patterns,
    uProbs,
    ...(conditioned === undefined ? {} : { conditioned }),
  });
}

describe("FS training from counts, in the shared kernel", () => {
  it("reproduces the Python reference for two learnable fields", () => {
    // Fixture case `two_level_learnable_only`.
    const out = train([
      { levels: [1, 1], count: 500 },
      { levels: [0, 1], count: 300 },
      { levels: [1, 0], count: 150 },
      { levels: [0, 0], count: 50 },
    ]);

    expect(cell(out.match_weights, 0, 0)).toBeCloseTo(-1.374233, 5);
    expect(cell(out.match_weights, 0, 1)).toBeCloseTo(2.706681, 5);
    // Field 1 too: asserting only field 0 would pass with the second field's
    // weights dropped or copied from the first.
    expect(cell(out.match_weights, 1, 0)).toBeCloseTo(-2.111677, 5);
    expect(cell(out.match_weights, 1, 1)).toBeCloseTo(2.421028, 5);
    expect(cell(out.u_probs, 0, 0)).toBeCloseTo(0.9, 9);
  });

  it("gives a conditioned field the bounded ramp and neutral u (#1835)", () => {
    // Fixture case `near_unique_blocking_field_1836`. A near-unique blocking
    // key whose u is LEARNED collapses toward the smoothing floor, which
    // explodes log2(m/u) past 20 bits and lets one field dominate every other
    // (measured F1 0.83 -> 0.57). Every wrong variant of this still returns a
    // valid probability vector, which is what makes it worth pinning.
    const out = train(
      [
        { levels: [1, 1], count: 500 },
        { levels: [0, 1], count: 300 },
      ],
      [
        [0.9, 0.1],
        [0.999, 0.001],
      ],
      [false, true],
    );

    expect(cell(out.match_weights, 1, 0)).toBeCloseTo(-3.0, 12);
    expect(cell(out.match_weights, 1, 1)).toBeCloseTo(3.0, 12);
    expect(cell(out.u_probs, 1, 0)).toBeCloseTo(0.5, 12);
    // Field 0 is free and must still be LEARNED -- if it also came back
    // [-3, 3] the rule is being applied to everything.
    expect(cell(out.match_weights, 0, 1)).not.toBeCloseTo(3.0, 6);
  });

  it("treats the counts as COUNTS, not proportions", () => {
    // Fixture case `weights_are_counts_not_proportions`: the same SHAPE at
    // 1/100th the counts must NOT give the same model. EM's 1e-6 smoothing is
    // additive, so its pull shrinks as the totals grow. A surface that
    // normalised the counts would pass every other test here and shift the
    // low-probability cells, which is where FS weights are largest.
    const shape: readonly (readonly number[])[] = [
      [1, 1],
      [0, 1],
      [1, 0],
      [0, 0],
    ];
    const withCounts = (counts: readonly number[]) =>
      shape.map((levels, i) => ({ levels, count: counts[i] as number }));

    const big = train(withCounts([500, 300, 150, 50]));
    const small = train(withCounts([5, 3, 1, 1]));

    expect(cell(small.match_weights, 0, 0)).not.toBeCloseTo(
      cell(big.match_weights, 0, 0),
      6,
    );
  });

  it("refuses a vector of the wrong width instead of misreading it", () => {
    expect(() => train([{ levels: [1], count: 10 }])).toThrow(
      /ordered by the matchkey/,
    );
  });

  it("surfaces a kernel refusal as a thrown Error, not a partial model", () => {
    // The kernel fails soft to an {"error": ...} envelope because SQL surfaces
    // must not abort a transaction; at a typed TS boundary that has to become a
    // throw, or callers destructure `undefined` weights and carry on.
    expect(() => train([{ levels: [1, 7], count: 10 }])).toThrow(/outside/);
  });
});
