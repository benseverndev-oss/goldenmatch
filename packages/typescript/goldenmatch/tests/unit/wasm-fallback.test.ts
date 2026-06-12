import { describe, it, expect, afterEach } from "vitest";
import { enableWasm, disableWasm } from "../../src/core/wasm/index.js";
import { getScorerBackend } from "../../src/core/wasm/backend.js";
import { scoreField } from "../../src/core/index.js";

describe("enableWasm graceful fallback", () => {
  afterEach(() => disableWasm());

  it("returns false and leaves pure-TS active when no artifact + no override", async () => {
    // Force the no-bytes path with an override that yields nothing.
    const ok = await enableWasm({ wasmBytes: new Uint8Array(0) });
    expect(ok).toBe(false);
    expect(getScorerBackend()).toBeNull();
    // Scoring still works (pure-TS).
    expect(scoreField("abc", "abc", "jaro_winkler")).toBe(1.0);
  });

  it("throws when require:true and bytes are unusable", async () => {
    await expect(
      enableWasm({ wasmBytes: new Uint8Array(0), require: true }),
    ).rejects.toThrow();
  });

  it("disableWasm resets to pure-TS", async () => {
    await enableWasm({ wasmBytes: new Uint8Array(0) });
    disableWasm();
    expect(getScorerBackend()).toBeNull();
  });
});
