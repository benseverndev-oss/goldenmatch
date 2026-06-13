import { describe, it, expect } from "vitest";
import {
  resolveWasmBytes,
  createBackendRegistry,
  enableWasmBackend,
} from "../src/index.js";

const DUMMY_URL = new URL("file:///nonexistent/artifact_bg.wasm");

describe("resolveWasmBytes", () => {
  it("returns explicit bytes", async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    expect(await resolveWasmBytes({ wasmBytes: bytes }, DUMMY_URL)).toBe(bytes);
  });
  it("throws on empty explicit bytes", async () => {
    await expect(
      resolveWasmBytes({ wasmBytes: new Uint8Array(0) }, DUMMY_URL),
    ).rejects.toThrow("empty wasmBytes");
  });
});

describe("createBackendRegistry", () => {
  it("defaults to null and round-trips set/get", () => {
    const r = createBackendRegistry<number>();
    expect(r.get()).toBeNull();
    r.set(42);
    expect(r.get()).toBe(42);
    r.set(null);
    expect(r.get()).toBeNull();
  });
});

describe("enableWasmBackend", () => {
  it("returns false on failure (no throw) and does not register", async () => {
    let registered: unknown = "untouched";
    const ok = await enableWasmBackend(
      { wasmBytes: new Uint8Array(0) }, // forces resolveWasmBytes to throw
      async () => ({ kind: "backend" }),
      (b) => {
        registered = b;
      },
      DUMMY_URL,
    );
    expect(ok).toBe(false);
    expect(registered).toBe("untouched");
  });

  it("throws under require:true", async () => {
    await expect(
      enableWasmBackend(
        { wasmBytes: new Uint8Array(0), require: true },
        async () => ({}),
        () => {},
        DUMMY_URL,
      ),
    ).rejects.toThrow();
  });

  it("instantiates + registers on success", async () => {
    let registered: { tag: string } | null = null;
    const ok = await enableWasmBackend(
      { wasmBytes: new Uint8Array([1]) },
      async () => ({ tag: "ok" }),
      (b) => {
        registered = b;
      },
      DUMMY_URL,
    );
    expect(ok).toBe(true);
    expect(registered).toEqual({ tag: "ok" });
  });
});
