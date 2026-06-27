import { describe, it, expect, afterEach } from "vitest";
import {
  setSuggestWasmBackend,
  getSuggestWasmBackend,
  disableSuggestWasm,
  isSuggestWasmEnabled,
  type SuggestWasmBackend,
} from "../../src/core/suggestWasmBackend.js";

const stub: SuggestWasmBackend = {
  suggestReview: () => "[]",
};

describe("SuggestWasmBackend registry", () => {
  afterEach(() => disableSuggestWasm());

  it("defaults to no backend (graceful-empty default)", () => {
    expect(getSuggestWasmBackend()).toBeNull();
    expect(isSuggestWasmEnabled()).toBe(false);
  });

  it("returns the registered backend after setSuggestWasmBackend", () => {
    setSuggestWasmBackend(stub);
    expect(getSuggestWasmBackend()).toBe(stub);
    expect(isSuggestWasmEnabled()).toBe(true);
  });

  it("disableSuggestWasm clears the backend", () => {
    setSuggestWasmBackend(stub);
    expect(getSuggestWasmBackend()).toBe(stub);
    disableSuggestWasm();
    expect(getSuggestWasmBackend()).toBeNull();
    expect(isSuggestWasmEnabled()).toBe(false);
  });
});
