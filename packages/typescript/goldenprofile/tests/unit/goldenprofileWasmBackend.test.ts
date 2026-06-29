/**
 * Backend registry + the unregistered-throw contract. resolveProfiles() must
 * REFUSE (throw an actionable error) when the wasm backend isn't enabled —
 * never silently return an empty/fake Resolution.
 */
import { describe, it, expect, afterEach } from "vitest";
import { resolveProfiles, isGoldenprofileWasmEnabled, disableGoldenprofileWasm } from "../../src/index.js";
import { enableGoldenprofileWasm } from "../../src/core/goldenprofileWasm.js";

const sample = {
  profiles: [
    { kind: "node" as const, name: "Acme Inc", category: "Company", anchor: "UNKNOWN", attribute: "Anvils" },
    { kind: "node" as const, name: "Acme", category: "Company", anchor: "UNKNOWN", attribute: "Founded 1900" },
  ],
};

describe("goldenprofile wasm backend registry", () => {
  afterEach(() => {
    disableGoldenprofileWasm();
  });

  it("throws an actionable error when wasm is not enabled", () => {
    disableGoldenprofileWasm();
    expect(isGoldenprofileWasmEnabled()).toBe(false);
    expect(() => resolveProfiles(sample)).toThrowError(/requires the wasm backend/i);
  });

  it("resolves once enabled, and reports enabled state", () => {
    enableGoldenprofileWasm();
    expect(isGoldenprofileWasmEnabled()).toBe(true);
    const out = resolveProfiles(sample);
    expect(out.clusters).toEqual([[0, 1]]);
    expect(out.edges.length).toBe(1);
  });

  it("disable restores the refusing state", () => {
    enableGoldenprofileWasm();
    expect(isGoldenprofileWasmEnabled()).toBe(true);
    disableGoldenprofileWasm();
    expect(isGoldenprofileWasmEnabled()).toBe(false);
    expect(() => resolveProfiles(sample)).toThrowError(/requires the wasm backend/i);
  });
});
