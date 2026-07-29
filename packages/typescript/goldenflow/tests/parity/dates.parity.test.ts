/**
 * Cross-surface byte-parity for the owned date kernel.
 *
 * The SAME oracle corpus (`packages/python/goldenflow/tests/parity/
 * dates_corpus.jsonl`, generated from the Python scalars which are byte-identical
 * to `goldenflow_core::dates`) is asserted against the pure-TS date transforms
 * run through the real `TransformEngine`. This file's corpus copy is byte-for-byte
 * identical to the Python one (CI's `goldenflow_wasm` corpus sync-check enforces
 * it), so the deterministic TS parser can never silently drift from Python/Rust.
 *
 * The four transforms here (`date_iso8601` / `date_us` / `date_eu` /
 * `datetime_iso8601`) are the fusable date kernels; the WASM leg that proves
 * pure-TS == the fused WASM `applyChain` runs in `fused_chain.parity.test.ts`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect } from "vitest";

import { TransformEngine } from "../../src/core/engine/transformer.js";
import type { Row } from "../../src/core/types.js";

interface CorpusRow {
  input: string;
  iso: string;
  us: string;
  eu: string;
  datetime: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const corpusPath = resolve(here, "dates_corpus.jsonl");
const CORPUS: CorpusRow[] = readFileSync(corpusPath, "utf8")
  .split("\n")
  .filter((l) => l.trim().length > 0)
  .map((l) => JSON.parse(l) as CorpusRow);

function applyColumn(transform: string, inputs: readonly string[]): ColumnResult[] {
  const rows: Row[] = inputs.map((s) => ({ v: s }));
  const engine = new TransformEngine({ transforms: [{ column: "v", ops: [transform] }] });
  return engine.transformDf(rows).rows.map((r) => r["v"] as ColumnResult);
}

type ColumnResult = string | null;

describe("goldenflow date kernel: pure-TS == Python/Rust oracle", () => {
  const inputs = CORPUS.map((r) => r.input);
  const kinds: Array<[string, keyof CorpusRow]> = [
    ["date_iso8601", "iso"],
    ["date_parse", "iso"], // alias of date_iso8601
    ["date_us", "us"],
    ["date_eu", "eu"],
    ["datetime_iso8601", "datetime"],
  ];

  for (const [transform, field] of kinds) {
    it(`${transform} matches the oracle across ${CORPUS.length} rows`, () => {
      const got = applyColumn(transform, inputs);
      const want = CORPUS.map((r) => r[field] as string);
      expect(got).toEqual(want);
    });
  }
});
