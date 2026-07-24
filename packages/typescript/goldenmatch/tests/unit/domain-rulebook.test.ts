/**
 * domain-rulebook.test.ts -- the user-defined domain rulebook port (parity batch 3).
 *
 * Covers the edge-safe core (compile / extract / matchDomain) and the node
 * registry round-trip (save -> discover -> load), which together back the
 * `list_domains` / `create_domain` / `test_domain` MCP tools.
 */
import { describe, it, expect, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  makeRulebook,
  compileRulebook,
  extractWithRulebook,
  matchDomain,
} from "../../src/core/domain-rulebook.js";
import { loadRulebook, saveRulebook } from "../../src/node/domain-registry.js";

const MEDICAL = makeRulebook("medical_devices", {
  signals: ["device", "ndc", "implant", "catheter"],
  identifierPatterns: { ndc: "\\b(\\d{5}-\\d{4}-\\d{2})\\b" },
  brandPatterns: ["Medtronic", "Abbott"],
  attributePatterns: { size: "\\b(\\d+\\.?\\d*)\\s*(mm|cm|fr)\\b" },
  stopWords: ["sterile", "single", "use", "disposable"],
});

const tmpDirs: string[] = [];
function newTmpDir(): string {
  const d = mkdtempSync(join(tmpdir(), "gm-domains-"));
  tmpDirs.push(d);
  return d;
}
afterEach(() => {
  for (const d of tmpDirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe("rulebook extraction", () => {
  it("pulls brand, identifier, attribute and a normalized name out of one string", () => {
    const c = compileRulebook(MEDICAL);
    const ex = extractWithRulebook(
      c,
      "Medtronic sterile catheter 12345-6789-01 6.5 fr single use",
    );
    expect(ex.brand).toBe("Medtronic");
    expect(ex.identifiers["ndc"]).toBe("12345-6789-01");
    expect(ex.attributes["size"]).toBe("6.5 fr");
    // stop words + the extracted spans are stripped; 1-char tokens dropped
    expect(ex.nameNormalized).toContain("catheter");
    expect(ex.nameNormalized).not.toContain("sterile");
    expect(ex.nameNormalized).not.toContain("12345");
    expect(ex.confidence).toBeGreaterThan(0);
    expect(ex.confidence).toBeLessThanOrEqual(1);
  });

  it("returns nulls (not throws) when nothing matches", () => {
    const ex = extractWithRulebook(compileRulebook(MEDICAL), "unrelated text");
    expect(ex.brand).toBeNull();
    expect(ex.identifiers).toEqual({});
    expect(ex.attributes).toEqual({});
  });

  it("skips an invalid regex instead of failing the whole rulebook", () => {
    const bad = makeRulebook("bad", {
      identifierPatterns: { good: "(\\d+)", broken: "([unclosed" },
    });
    const c = compileRulebook(bad);
    expect(c.invalid).toContain("identifier:broken");
    expect(c.identifiers.has("good")).toBe(true);
    // the surviving pattern still works
    expect(extractWithRulebook(c, "order 42").identifiers["good"]).toBe("42");
  });

  it("escapes brand literals so regex metacharacters are matched literally", () => {
    const rb = makeRulebook("b", { brandPatterns: ["Johnson & Johnson"] });
    const ex = extractWithRulebook(compileRulebook(rb), "a Johnson & Johnson stent");
    expect(ex.brand).toBe("Johnson & Johnson");
  });
});

describe("matchDomain", () => {
  it("picks the rulebook whose signals appear in the column names", () => {
    const other = makeRulebook("automotive", { signals: ["vin", "trim"] });
    const best = matchDomain(["device_name", "ndc_code"], [other, MEDICAL]);
    expect(best?.name).toBe("medical_devices");
  });

  it("returns null when nothing scores", () => {
    expect(matchDomain(["a", "b"], [MEDICAL])).toBeNull();
  });
});

describe("registry round-trip", () => {
  it("saves to YAML and loads back an equivalent rulebook", () => {
    const dir = newTmpDir();
    const path = saveRulebook(MEDICAL, join(dir, "medical_devices.yaml"));
    const back = loadRulebook(path);
    expect(back.name).toBe(MEDICAL.name);
    expect([...back.signals]).toEqual([...MEDICAL.signals]);
    expect(back.identifierPatterns).toEqual(MEDICAL.identifierPatterns);
    expect([...back.brandPatterns]).toEqual([...MEDICAL.brandPatterns]);
    expect([...back.stopWords]).toEqual([...MEDICAL.stopWords]);
  });

  it("reads Python's snake_case YAML keys", () => {
    const dir = newTmpDir();
    const p = join(dir, "auto.yaml");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      p,
      [
        "name: automotive",
        "signals:",
        "  - vin",
        "identifier_patterns:",
        '  vin: "\\\\b([A-HJ-NPR-Z0-9]{17})\\\\b"',
        "brand_patterns:",
        "  - Toyota",
        "stop_words:",
        "  - used",
        "",
      ].join("\n"),
      "utf-8",
    );
    const rb = loadRulebook(p);
    expect(rb.name).toBe("automotive");
    expect(Object.keys(rb.identifierPatterns)).toEqual(["vin"]);
    expect([...rb.brandPatterns]).toEqual(["Toyota"]);
  });

  it("falls back to the filename when the YAML omits `name`", () => {
    const dir = newTmpDir();
    const p = join(dir, "fallback_name.yaml");
    writeFileSync(p, "signals:\n  - x\n", "utf-8");
    expect(loadRulebook(p).name).toBe("fallback_name");
  });
});
