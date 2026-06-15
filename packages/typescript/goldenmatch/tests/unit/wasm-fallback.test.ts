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

  it("empty wasmBase64 falls back to pure-TS (no throw without require)", async () => {
    // The universal-loader byte source: an empty/garbage base64 must NOT crash
    // the default path — it falls back exactly like absent bytes.
    const ok = await enableWasm({ wasmBase64: "" });
    expect(typeof ok).toBe("boolean");
    expect(scoreField("abc", "abc", "levenshtein")).toBe(1.0);
  });

  it("universal:true returns a boolean and never disturbs pure-TS scoring", async () => {
    // Whether or not the inlined base64 module is present in THIS checkout, the
    // opt-in universal path must resolve to true/false (never throw without
    // require) and leave pure-TS scoring intact.
    const ok = await enableWasm({ universal: true });
    expect(typeof ok).toBe("boolean");
    expect(scoreField("abc", "abc", "exact")).toBe(1.0);
  });
});
