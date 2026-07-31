/**
 * Pure-TS evidence-set builder for denial-constraint discovery. Parity port of
 * the pure-Python fallback `_evidence_python` in
 * `packages/python/goldencheck/goldencheck/denial/evidence.py`.
 *
 * MASKS ARE u64 → BIGINT. JS `number` bitwise ops are 32-bit and would silently
 * corrupt masks above bit 31, so every mask is a `bigint` and evidence maps are
 * `Map<bigint, number>`.
 *
 * Two passes build integer *satisfaction masks* counted into `mask → count`:
 * - **Pass 1** (one u64 per row): bit `i` (`0 <= i < s`) = `singles[i]` holds on
 *   the row. Crosses do NOT participate.
 * - **Pass 2** (one u64 per ordered pair `(α, β)`, `α != β`):
 *     bit `i`        = `singles[i]` on α
 *     bit `s + i`    = `singles[i]` on β
 *     bit `2s + j`   = `crosses[j]` on `(α, β)`
 *   Iterates ALL ordered pairs over `sampleIdx` (both `(α,β)` and `(β,α)`).
 */

import { KIND_CODE, OP_CODE, type PredicateSpace } from "./predicates.js";

/** Flat kernel-arg spec per predicate: (kindCode, colA, opCode, colB, literalId). */
export type PredSpec = readonly [number, number, number, number, number];

/**
 * Flatten a `PredicateSpace` into plain-array kernel form.
 * Returns `{cols, nulls, predSpec}` in the SAME predicate order as the space.
 */
export function spaceToKernelArgs(space: PredicateSpace): {
  cols: number[][];
  nulls: boolean[][];
  predSpec: PredSpec[];
} {
  const order = [...space.enc.keys()];
  const colIndex = new Map<string, number>();
  order.forEach((name, i) => colIndex.set(name, i));
  const cols = order.map((name) => [...space.enc.get(name)!.ids]);
  const nulls = order.map((name) => [...space.enc.get(name)!.nulls]);

  const predSpec: PredSpec[] = [];
  for (const p of space.predicates) {
    const kindCode = KIND_CODE[p.kind];
    const opCode = OP_CODE[p.op];
    const colA = colIndex.get(p.colA)!;
    if (p.kind === "const") {
      const literalId = space.enc.get(p.colA)!.idOfValue.get(p.literal as never) ?? 0;
      predSpec.push([kindCode, colA, opCode, 0, literalId]);
    } else {
      const colB = colIndex.get(p.colB!)!;
      predSpec.push([kindCode, colA, opCode, colB, 0]);
    }
  }
  return { cols, nulls, predSpec };
}

function cmpCode(op: number, x: number, y: number): boolean {
  switch (op) {
    case 0:
      return x === y;
    case 1:
      return x !== y;
    case 2:
      return x < y;
    case 3:
      return x <= y;
    case 4:
      return x > y;
    default:
      return x >= y; // 5 = GE
  }
}

function holdsSingleTuple(spec: PredSpec, cols: number[][], nulls: boolean[][], r: number): boolean {
  const [kind, colA, op, colB, literal] = spec;
  if (nulls[colA]![r]) return false;
  if (kind === 0) return cmpCode(op, cols[colA]![r]!, literal); // const
  if (nulls[colB]![r]) return false; // single
  return cmpCode(op, cols[colA]![r]!, cols[colB]![r]!);
}

function holdsCross(spec: PredSpec, cols: number[][], nulls: boolean[][], a: number, b: number): boolean {
  const [, colA, op, colB] = spec;
  if (nulls[colA]![a] || nulls[colB]![b]) return false;
  return cmpCode(op, cols[colA]![a]!, cols[colB]![b]!);
}

function bump(hist: Map<bigint, number>, mask: bigint): void {
  hist.set(mask, (hist.get(mask) ?? 0) + 1);
}

/** Cols-based pure-TS evidence map — byte-exact port of `_evidence_python`. */
function evidence(
  cols: number[][],
  nulls: boolean[][],
  predSpec: readonly PredSpec[],
  whichPass: 1 | 2,
  n: number,
  sampleIdx: readonly number[],
): Map<bigint, number> {
  const singles = predSpec.filter((spec) => spec[0] !== 2);
  const crosses = predSpec.filter((spec) => spec[0] === 2);
  const hist = new Map<bigint, number>();

  if (whichPass === 1) {
    for (let r = 0; r < n; r++) {
      let mask = 0n;
      for (let i = 0; i < singles.length; i++) {
        if (holdsSingleTuple(singles[i]!, cols, nulls, r)) mask |= 1n << BigInt(i);
      }
      bump(hist, mask);
    }
    return hist;
  }

  const s = singles.length;
  for (const alpha of sampleIdx) {
    let alphaMask = 0n;
    for (let i = 0; i < s; i++) {
      if (holdsSingleTuple(singles[i]!, cols, nulls, alpha)) alphaMask |= 1n << BigInt(i);
    }
    for (const beta of sampleIdx) {
      if (alpha === beta) continue;
      let mask = alphaMask;
      for (let i = 0; i < s; i++) {
        if (holdsSingleTuple(singles[i]!, cols, nulls, beta)) mask |= 1n << BigInt(s + i);
      }
      for (let j = 0; j < crosses.length; j++) {
        if (holdsCross(crosses[j]!, cols, nulls, alpha, beta)) mask |= 1n << BigInt(2 * s + j);
      }
      bump(hist, mask);
    }
  }
  return hist;
}

/** Pass 1: mask → row-count over the `n` rows. */
export function rowEvidence(space: PredicateSpace, n: number): Map<bigint, number> {
  const { cols, nulls, predSpec } = spaceToKernelArgs(space);
  return evidence(cols, nulls, predSpec, 1, n, []);
}

/** Pass 2: mask → pair-count over all ordered pairs `(α, β)`, `α != β`. */
export function pairEvidence(space: PredicateSpace, sampleIdx: readonly number[]): Map<bigint, number> {
  const { cols, nulls, predSpec } = spaceToKernelArgs(space);
  return evidence(cols, nulls, predSpec, 2, 0, sampleIdx);
}
