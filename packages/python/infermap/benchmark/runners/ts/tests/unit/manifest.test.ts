import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadManifest,
  InvalidManifestError,
  IncompatibleManifestError,
} from "../../src/manifest.js";
import { MANIFEST_VERSION } from "../../src/index.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "bench-ts-manifest-"));
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function writeManifest(data: unknown): string {
  const path = join(tmp, "manifest.json");
  writeFileSync(path, JSON.stringify(data), "utf8");
  return path;
}

function validEntry(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "valentine/magellan/foo",
    path: "cases/valentine/foo",
    category: "valentine",
    subcategory: "magellan",
    source: {
      name: "foo",
      url: "https://example.com",
      license: "MIT",
      attribution: "test",
    },
    tags: ["alias_dominant"],
    expected_difficulty: "easy",
    field_counts: { source: 4, target: 4 },
    ...overrides,
  };
}

describe("loadManifest", () => {
  it("loads a valid manifest", () => {
    const path = writeManifest({
      version: 1,
      generated_at: "2026-04-08T00:00:00Z",
      cases: [validEntry()],
    });
    const refs = loadManifest(path);
    expect(refs).toHaveLength(1);
    const ref = refs[0]!;
    expect(ref.id).toBe("valentine/magellan/foo");
    expect(ref.category).toBe("valentine");
    expect(ref.expectedDifficulty).toBe("easy");
    expect(ref.tags).toEqual(["alias_dominant"]);
    expect(ref.fieldCounts).toEqual({ source: 4, target: 4 });
  });

  it("rejects newer version with IncompatibleManifestError", () => {
    const path = writeManifest({
      version: MANIFEST_VERSION + 1,
      generated_at: "x",
      cases: [],
    });
    expect(() => loadManifest(path)).toThrow(IncompatibleManifestError);
  });

  it("rejects missing version", () => {
    const path = writeManifest({ cases: [] });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects boolean version", () => {
    const path = writeManifest({ version: true, generated_at: "x", cases: [] });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects missing cases", () => {
    const path = writeManifest({ version: 1, generated_at: "x" });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects non-array cases", () => {
    const path = writeManifest({ version: 1, generated_at: "x", cases: "nope" });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects invalid category", () => {
    const path = writeManifest({
      version: 1, generated_at: "x",
      cases: [validEntry({ category: "bogus" })],
    });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects invalid difficulty", () => {
    const path = writeManifest({
      version: 1, generated_at: "x",
      cases: [validEntry({ expected_difficulty: "impossible" })],
    });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects source missing license", () => {
    const bad = validEntry();
    delete (bad.source as Record<string, unknown>).license;
    const path = writeManifest({ version: 1, generated_at: "x", cases: [bad] });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects non-string tag", () => {
    const path = writeManifest({
      version: 1, generated_at: "x",
      cases: [validEntry({ tags: ["ok", 42] })],
    });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects field_counts missing target", () => {
    const path = writeManifest({
      version: 1, generated_at: "x",
      cases: [validEntry({ field_counts: { source: 4 } })],
    });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("rejects field_counts with non-integer value", () => {
    const path = writeManifest({
      version: 1, generated_at: "x",
      cases: [validEntry({ field_counts: { source: "four", target: 4 } })],
    });
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });

  it("throws generic Error on missing file", () => {
    expect(() => loadManifest(join(tmp, "does_not_exist.json"))).toThrow(/not found/);
  });

  it("throws InvalidManifestError on malformed JSON", () => {
    const path = join(tmp, "manifest.json");
    writeFileSync(path, "{not json", "utf8");
    expect(() => loadManifest(path)).toThrow(InvalidManifestError);
  });
});
