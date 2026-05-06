import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { describe, it, expect, afterEach } from "vitest";
import { loadDomain, listDomains, clearCache } from "../src/loader.js";

describe("loader", () => {
  it("listDomains includes finance, healthcare, ecommerce, generic", () => {
    const d = listDomains();
    expect(d).toContain("finance");
    expect(d).toContain("healthcare");
    expect(d).toContain("ecommerce");
    expect(d).toContain("generic");
  });

  it("loads finance pack", () => {
    const pack = loadDomain("finance");
    expect(pack.name).toBe("finance");
    expect(pack.types["account_number"]).toBeDefined();
  });

  it("throws on unknown pack", () => {
    expect(() => loadDomain("does_not_exist")).toThrow();
  });

  it("generic pack is empty", () => {
    const pack = loadDomain("generic");
    expect(pack.types).toEqual({});
  });
});

describe("loader cache", () => {
  let tmpDir: string | null = null;
  const orig = process.env.GOLDENCHECK_TYPES_TEST_DIR;

  afterEach(() => {
    if (orig === undefined) delete process.env.GOLDENCHECK_TYPES_TEST_DIR;
    else process.env.GOLDENCHECK_TYPES_TEST_DIR = orig;
    if (tmpDir && fs.existsSync(tmpDir)) {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
    tmpDir = null;
    clearCache();
  });

  it("returns the same object instance on repeat loads", () => {
    clearCache();
    const a = loadDomain("finance");
    const b = loadDomain("finance");
    expect(a).toBe(b);
  });

  it("clearCache forces a re-read after on-disk mutation", () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "gct-"));
    process.env.GOLDENCHECK_TYPES_TEST_DIR = tmpDir;
    clearCache();

    const file = path.join(tmpDir, "fakedomain.yaml");
    fs.writeFileSync(
      file,
      "description: v1\ntypes:\n  email:\n    name_hints: [a]\n",
    );
    const p1 = loadDomain("fakedomain");
    expect(p1.description).toBe("v1");

    fs.writeFileSync(
      file,
      "description: v2\ntypes:\n  email:\n    name_hints: [b]\n",
    );
    // Cached value still wins without clear.
    const p2 = loadDomain("fakedomain");
    expect(p2.description).toBe("v1");

    clearCache();
    const p3 = loadDomain("fakedomain");
    expect(p3.description).toBe("v2");
  });
});
