/**
 * Cross-surface parity: the record-fingerprint wasm kernel reproduces the SAME
 * 64-hex digest as the Rust `fingerprint-core` (`tests/golden.rs`) and the
 * Python-native / DuckDB / Postgres surfaces — the shared `fingerprint_golden
 * .json` oracle. One canonicalizer (field sort, type tags, separators, float
 * bits, `__`-drop) proven identical on every surface.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { fingerprintJson } from "../../src/core/fingerprintWasm.js";

interface GoldenCase {
  name: string;
  json: string;
  hash: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const cases: GoldenCase[] = JSON.parse(
  readFileSync(resolve(here, "fixtures/fingerprint/fingerprint_golden.json"), "utf8"),
);

describe("fingerprint-wasm parity — reproduces the shared golden fixture", () => {
  it("has broad case coverage", () => {
    expect(cases.length).toBeGreaterThanOrEqual(10);
  });

  for (const c of cases) {
    it(`fingerprint_json: ${c.name}`, () => {
      expect(fingerprintJson(c.json)).toBe(c.hash);
    });
  }

  it("rejects invalid input (non-object / bad JSON)", () => {
    expect(() => fingerprintJson("not json")).toThrow();
    expect(() => fingerprintJson("[1,2,3]")).toThrow();
    expect(() => fingerprintJson('{"a":[1]}')).toThrow();
  });
});
