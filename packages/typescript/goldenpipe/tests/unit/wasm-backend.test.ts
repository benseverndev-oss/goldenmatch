import { describe, it, expect, afterEach } from "vitest";
import {
  setPipeWasmBackend,
  getPipeWasmBackend,
  type PipeWasmBackend,
} from "../../src/core/wasm/backend.js";

const fake: PipeWasmBackend = {
  resolveJson: () => "{}",
  applyDecisionJson: () => "{}",
  evaluateBuiltinJson: () => "null",
  autoConfigJson: () => "{}",
  skipIfFalsyJson: () => "true",
};

describe("PipeWasmBackend registry", () => {
  afterEach(() => setPipeWasmBackend(null));

  it("is null by default", () => {
    expect(getPipeWasmBackend()).toBeNull();
  });
  it("set/get round-trips and reset isolates", () => {
    setPipeWasmBackend(fake);
    expect(getPipeWasmBackend()).toBe(fake);
    setPipeWasmBackend(null);
    expect(getPipeWasmBackend()).toBeNull();
  });
});
