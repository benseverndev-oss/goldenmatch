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
