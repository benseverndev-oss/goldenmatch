/**
 * Cross-surface parity for `classify_by_name` — the name-*pattern*-only classifier
 * the #1207 strong-identifier blocking union uses for name-column detection.
 *
 * The fixture `classify_by_name_vectors.json` is the shared oracle — a
 * byte-identical copy of
 * `autoconfig-core/golden/classify_by_name_vectors.json` (authored from Python
 * `_classify_by_name`, checked by the Rust golden test). This asserts BOTH TS
 * paths reproduce it:
 *   - the pure-TS port (`classifyByName`, the always-on runtime path), and
 *   - the shared wasm core (the `autoconfig_classify_by_name` shim).
 * So TS-pure == wasm == Rust == Python on name classification — the divergence
 * that made the union over-fire in the 2a→2b split cannot recur.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, it, expect } from "vitest";

import { classifyByName } from "../../src/core/classifyByName.js";
import { classifyByNameRawJson } from "../../src/core/autoconfigWasm.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(
    resolve(__dirname, "fixtures/classify-by-name/classify_by_name_vectors.json"),
    "utf8",
  ),
) as { name: string; expected: string | null }[];

describe("classify_by_name golden parity", () => {
  it("fixture has broad coverage", () => {
    expect(fixture.length).toBeGreaterThanOrEqual(40);
  });

  for (const c of fixture) {
    it(`pure-TS: ${JSON.stringify(c.name)} -> ${c.expected}`, () => {
      expect(classifyByName(c.name)).toEqual(c.expected);
    });
    it(`wasm: ${JSON.stringify(c.name)} -> ${c.expected}`, () => {
      const got = JSON.parse(classifyByNameRawJson(JSON.stringify({ name: c.name })));
      expect(got).toEqual(c.expected);
    });
  }
});
