/**
 * Proves the key-integrity-core wasm reroute is behavior-preserving:
 * `certifyKeyIntegrity(table)` produces the SAME structural certificate with the
 * wasm backend enabled (the shared kernel) and disabled (the pure-TS fallback).
 * The reroute swaps only the group-by uniqueness + fan-out reduction; the loader
 * JSON-normalizes values so the kernel's grouping matches the pure-TS `groupKey`.
 */
import { describe, it, expect, afterEach } from "vitest";

import { certifyKeyIntegrity } from "../../src/core/semantic/keyIntegrity.js";
import { enableKeyIntegrityWasm, disableKeyIntegrityWasm } from "../../src/core/keyIntegrityWasm.js";
import { isKeyIntegrityWasmEnabled } from "../../src/core/keyIntegrityWasmBackend.js";

afterEach(() => disableKeyIntegrityWasm());

type Table = Record<string, readonly unknown[]>;
type Opts = Parameters<typeof certifyKeyIntegrity>[1];

function structural(cert: ReturnType<typeof certifyKeyIntegrity>) {
  return {
    nKeyGroups: cert.nKeyGroups,
    duplicateKeyGroups: cert.duplicateKeyGroups,
    maxFanOut: cert.maxFanOut,
    isUniqueAtGrain: cert.isUniqueAtGrain,
    measureFanOut: cert.measureFanOut,
    estimate: cert.estimate,
  };
}

const CASES: { name: string; table: Table; opts: Opts }[] = [
  {
    name: "unique string key, no measures",
    table: { id: ["e1", "e2", "e3"] },
    opts: { key: "id" },
  },
  {
    name: "duplicated key with a measure",
    table: { id: ["e1", "e1", "e2", "e3"], amt: [10, 30, 8, 40] },
    opts: { key: "id", measures: ["amt"] },
  },
  {
    name: "compound key + float measure",
    table: { src: ["a", "a", "a", "b"], pk: [1, 1, 2, 1], rev: [5.5, 15.5, 3.0, 9.0] },
    opts: { key: ["src", "pk"], measures: ["rev"] },
  },
  {
    name: "grain-strict grouping",
    table: {
      customer_id: [1, 1, 2, 2],
      day: ["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"],
      spend: [10, 20, 5, 7],
    },
    opts: { key: "customer_id", measures: ["spend"], grain: ["day"], grainStrict: true },
  },
  {
    name: "measure with nulls",
    table: { id: ["e1", "e1", "e2"], amt: [10, null, 4] },
    opts: { key: "id", measures: ["amt"] },
  },
];

describe("key-integrity wasm reroute == pure-TS", () => {
  for (const c of CASES) {
    it(`${c.name}`, () => {
      const pure = structural(certifyKeyIntegrity(c.table, c.opts));

      enableKeyIntegrityWasm();
      expect(isKeyIntegrityWasmEnabled()).toBe(true);
      const wasm = structural(certifyKeyIntegrity(c.table, c.opts));

      expect(wasm).toEqual(pure);
    });
  }

  it("default (backend disabled) uses the pure-TS path", () => {
    expect(isKeyIntegrityWasmEnabled()).toBe(false);
    const cert = certifyKeyIntegrity({ id: ["e1", "e1", "e2"] }, { key: "id" });
    expect(cert.duplicateKeyGroups).toBe(1);
    expect(cert.maxFanOut).toBe(2);
  });
});
