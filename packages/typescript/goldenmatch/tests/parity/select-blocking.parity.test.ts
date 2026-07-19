/**
 * Cross-surface parity for the #1207 strong-identifier blocking union.
 *
 * The fixture `select_blocking_vectors.json` is the shared oracle — a
 * byte-identical copy of `autoconfig-core/golden/select_blocking_vectors.json`
 * (checked by the Rust golden test, and by the Python equivalence test in
 * increment 3). This asserts that BOTH TS decision paths reproduce it:
 *   - the pure-TS port (`blockingUnion.ts`, the always-on runtime path), and
 *   - the shared wasm core (the `autoconfig_*_strong_id_union` shims).
 * So TS-pure == wasm == Rust == Python on the union's assemble + finalize
 * decision — the drift #1317 was about cannot recur.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect } from "vitest";

import {
  assembleStrongIdUnion,
  finalizeStrongIdUnion,
  type UnionColumn,
  type UnionPass,
} from "../../src/core/blockingUnion.js";
import {
  assembleStrongIdUnionRawJson,
  finalizeStrongIdUnionRawJson,
} from "../../src/core/autoconfigWasm.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(
    resolve(__dirname, "fixtures/select-blocking/select_blocking_vectors.json"),
    "utf8",
  ),
) as {
  assemble: {
    name: string;
    input: Array<{
      name: string;
      col_type: string;
      null_rate: number;
      cardinality_ratio: number;
    }>;
    expected: unknown;
  }[];
  finalize: {
    name: string;
    input: {
      passes: Array<{ fields: string[]; transforms: string[]; is_strong_id: boolean }>;
      coverage: number;
      pass_survives: boolean[];
      max_safe_block: number;
    };
    expected: unknown;
  }[];
};

// snake_case (the wire/fixture shape) <-> the pure-TS camelCase types.
const toUnionColumn = (c: {
  name: string;
  col_type: string;
  null_rate: number;
  cardinality_ratio: number;
}): UnionColumn => ({
  name: c.name,
  colType: c.col_type,
  nullRate: c.null_rate,
  cardinalityRatio: c.cardinality_ratio,
});
const passToSnake = (p: UnionPass) => ({
  fields: [...p.fields],
  transforms: [...p.transforms],
  is_strong_id: p.isStrongId,
});
const passFromSnake = (p: {
  fields: string[];
  transforms: string[];
  is_strong_id: boolean;
}): UnionPass => ({
  fields: p.fields,
  transforms: p.transforms,
  isStrongId: p.is_strong_id,
});

describe("select_blocking golden parity — assemble", () => {
  for (const c of fixture.assemble) {
    it(`wasm: ${c.name}`, () => {
      const got = JSON.parse(assembleStrongIdUnionRawJson(JSON.stringify(c.input)));
      expect(got).toEqual(c.expected);
    });
    it(`pure-TS: ${c.name}`, () => {
      const out = assembleStrongIdUnion(c.input.map(toUnionColumn));
      const got = out === null ? null : out.map(passToSnake);
      expect(got).toEqual(c.expected);
    });
  }
});

describe("select_blocking golden parity — finalize", () => {
  for (const c of fixture.finalize) {
    it(`wasm: ${c.name}`, () => {
      const got = JSON.parse(finalizeStrongIdUnionRawJson(JSON.stringify(c.input)));
      expect(got).toEqual(c.expected);
    });
    it(`pure-TS: ${c.name}`, () => {
      const out = finalizeStrongIdUnion(
        c.input.passes.map(passFromSnake),
        c.input.coverage,
        c.input.pass_survives,
        c.input.max_safe_block,
      );
      const got =
        out === null
          ? null
          : {
              strategy: out.strategy,
              keys: out.keys.map(passToSnake),
              passes: out.passes.map(passToSnake),
              max_block_size: out.maxBlockSize,
              skip_oversized: out.skipOversized,
            };
      expect(got).toEqual(c.expected);
    });
  }
});
