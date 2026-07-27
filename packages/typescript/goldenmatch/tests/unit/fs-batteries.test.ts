/**
 * fs-batteries.test.ts -- the batteries-included `goldenmatch` entry
 * (fs-default-ts-path: bundle-default-on).
 *
 * The bare package root (`src/index.ts`) must auto-register the shared fs-core
 * kernel on import, so `dedupe()`/`match()` land on Python-native's operating
 * point with no `enableFsWasmScoring()` call. The lean `goldenmatch/core` entry
 * stays opt-in (asserted in fs-reroute.test.ts, which imports only core and
 * expects the null default). Vitest isolates the module registry per file, so
 * importing the batteries entry here does not leak the enabled backend into the
 * core-only test files.
 */
import { describe, it, expect } from "vitest";
import * as batteries from "../../src/index.js";
import {
  getFsScoreBackend,
  isFsWasmScoringEnabled,
} from "../../src/core/fsScoreBackend.js";

describe("batteries-included entry (goldenmatch root)", () => {
  it("auto-registers the fs-core kernel on import (no opt-in call)", () => {
    expect(isFsWasmScoringEnabled()).toBe(true);
    expect(getFsScoreBackend()).not.toBeNull();
  });

  it("re-exports the full core surface (dedupe/match present)", () => {
    expect(typeof batteries.dedupe).toBe("function");
    expect(typeof batteries.match).toBe("function");
  });

  it("re-exports the kernel opt-out / opt-in controls", () => {
    expect(typeof batteries.disableFsWasmScoring).toBe("function");
    expect(typeof batteries.enableFsWasmScoring).toBe("function");

    // Opt-out restores the pure-TS fallback...
    batteries.disableFsWasmScoring();
    expect(isFsWasmScoringEnabled()).toBe(false);
    expect(getFsScoreBackend()).toBeNull();

    // ...and re-enabling is idempotent (mirrors the sibling wasm reroutes).
    batteries.enableFsWasmScoring();
    batteries.enableFsWasmScoring();
    expect(isFsWasmScoringEnabled()).toBe(true);
  });
});
