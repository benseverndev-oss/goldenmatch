/**
 * WASM-vs-pure-TS parity for infermap `detect`. The WASM backend wraps
 * infermap-core::detect_domain (== Python == the Rust FFI); scoreDomains is the
 * pure-TS reimplementation. This gate asserts they agree byte-for-byte over a
 * synthetic domain corpus (the Wave-1 kernel-parity cases, non-empty columns).
 *
 * Skipped when the built artifact is absent (default checkout / no toolchain);
 * the CI `infermap_wasm` lane builds it first and runs this un-skipped. Any
 * DISAGREEMENT is a real TS-vs-Rust `detect` drift finding (hintMatches token
 * logic, tie order) — WASM is the reference; surface it, don't skip it.
 */
import { describe, it, expect, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { scoreDomains } from "../../src/core/detect.js";
import {
  enableInfermapWasm,
  disableInfermapWasm,
} from "../../src/core/wasm/index.js";
import { getInfermapBackend } from "../../src/core/wasm/backend.js";
import { jaroWinklerSimilarity } from "../../src/core/util/string-distance.js";
import { scorePair } from "../../src/core/scorers/initialism.js";

const artifact = fileURLToPath(
  new URL("../../src/core/wasm/artifacts/infermap_wasm_bg.wasm", import.meta.url),
);
const d = existsSync(artifact) ? describe : describe.skip;

// (columns, domains [name,hints[]][], minScore) — non-empty columns only.
// Mirrors infermap Python test_native_parity._CASES (minus the empty-columns case).
type Case = [string[], Array<[string, string[]]>, number];
const CASES: Case[] = [
  [["provider_npi", "first_name"], [["health", ["provider npi"]], ["fin", ["iban"]]], 0.3], // confident
  [["a", "b"], [["x", ["a"]], ["y", ["b"]]], 0.3], // 2-way tie
  [["a", "b"], [["x", ["a"]], ["y", ["b"]], ["z", ["a"]]], 0.3], // 3-way tie (host order)
  [["a", "b", "c", "d"], [["h", ["a"]]], 0.3], // below_min_score (0.25)
  [["a"], [["h", []]], 0.3], // no_data (all hint-less)
  [["patient_id", "provider_npi", "dob"], [["health", ["patient id", "npi"]], ["fin", ["iban"]]], 0.3],
  [["a"], [["h", ["a b c"]]], 0.3], // hint longer than column
  [["ORDER_ID", "Sku"], [["ecom", ["order id", "sku"]]], 0.3], // ASCII case-insensitivity
];

d("infermap detect WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());

  it("enableInfermapWasm() succeeds in this lane (artifact present)", async () => {
    disableInfermapWasm();
    const ok = await enableInfermapWasm({ require: true });
    expect(ok).toBe(true);
    disableInfermapWasm();
  });

  for (let i = 0; i < CASES.length; i++) {
    const [columns, domains, minScore] = CASES[i]!;
    it(`case ${i}: kernel == scoreDomains`, async () => {
      const pure = scoreDomains(columns, domains, minScore);
      const ok = await enableInfermapWasm({ require: true });
      expect(ok).toBe(true);
      const backend = getInfermapBackend()!;
      const wasm = backend.detectDomain(columns, domains, minScore);
      disableInfermapWasm();
      expect(wasm).toEqual(pure); // deep-equal DetectionResult; drift => fail
    });
  }
});

// ---------------------------------------------------------------------------
// Wave B: name-scorer parity (drift audit — 2 JW impls, 2 tokenizers)
// ---------------------------------------------------------------------------

const normalize = (s: string): string =>
  s.trim().toLowerCase().replace(/[_\- ]/g, "");
const pureExact = (a: string, b: string): number =>
  a.trim().toLowerCase() === b.trim().toLowerCase() ? 1.0 : 0.0;

// ASCII pairs — mirrors the Python Wave 2 _NAME_PAIRS. The toLowerCase / chars()
// Unicode edges stay out of the must-pass corpus (Wave 1/2 documented boundary).
const NAME_PAIRS: Array<[string, string]> = [
  ["City", "city"],
  ["provider_npi", "ProviderNPI"],
  ["first_name", "firstName"],
  ["assay_id", "ASSI"],
  ["confidence_score", "CONSC"],
  ["variant_id", "VARI"],
  ["order_id", "orderid"],
  ["abc", "xyz"],
  ["HTTPSConnection", "https_connection"],
  ["a", "a"],
  ["dob", "date_of_birth"],
  ["providerIDs", "provider_i_ds"],
  ["URLs", "ur_ls"],
  ["macOS", "mac_os"],
  ["iOS", "i_os"],
];

d("infermap name-scorer WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());

  for (const [a, b] of NAME_PAIRS) {
    it(`exact '${a}'/'${b}' kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      expect(be.exactScore(a, b)).toBe(pureExact(a, b));
      disableInfermapWasm();
    });
    it(`fuzzy '${a}'/'${b}' kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      expect(be.fuzzyNameScore(a, b)).toBe(
        jaroWinklerSimilarity(normalize(a), normalize(b)),
      );
      disableInfermapWasm();
    });
    it(`initialism '${a}'/'${b}' kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      expect(be.initialismScore(a, b)).toBe(scorePair(a, b));
      disableInfermapWasm();
    });
  }
});
