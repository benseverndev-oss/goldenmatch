import { describe, it, expect } from "vitest";

import { diagnoseConfig, type CritiqueRunLike } from "../../src/core/config-critique.js";
import type { GoldenMatchConfig, Row, WeightedMatchkey } from "../../src/core/types.js";

function weightedOn(field: string, threshold = 0.85): WeightedMatchkey {
  return {
    name: "mk",
    type: "weighted",
    threshold,
    fields: [{ field, transforms: ["lowercase"], scorer: "jaro_winkler", weight: 1.0 }],
  };
}

const emptyResult: CritiqueRunLike = {};

describe("config-critique: diagnoseConfig", () => {
  it("flags a per-row id column admitted as a matching signal (id_admitted)", () => {
    const rows: Row[] = Array.from({ length: 6 }, (_, i) => ({
      record_id: `id-${i}`,
      name: i < 3 ? "jane" : "john",
    }));
    const config: GoldenMatchConfig = { matchkeys: [weightedOn("record_id")] };
    const out = diagnoseConfig(rows, config, emptyResult);

    const ids = out.findings.map((f) => f.id);
    expect(ids).toContain("id_admitted");
    const f = out.findings.find((x) => x.id === "id_admitted")!;
    expect(f.severity).toBe("high");
    expect(f.evidence["column"]).toBe("record_id");
    expect(f.fix_config_hint).toEqual({ action: "exclude_column", column: "record_id" });
  });

  it("flags a source/provenance label admitted as a matching signal (source_admitted)", () => {
    const rows: Row[] = [
      { source: "crm", name: "jane" },
      { source: "billing", name: "jane" },
    ];
    const config: GoldenMatchConfig = { matchkeys: [weightedOn("source")] };
    const out = diagnoseConfig(rows, config, emptyResult);

    const f = out.findings.find((x) => x.id === "source_admitted");
    expect(f).toBeDefined();
    expect(f?.severity).toBe("high");
    expect(f?.evidence["column"]).toBe("source");
  });

  it("flags a mostly-empty matchkey column (null_sink)", () => {
    const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
      middle: i < 2 ? "q" : null, // 80% null -> null_sink
      name: `person-${i}`,
    }));
    const config: GoldenMatchConfig = { matchkeys: [weightedOn("middle")] };
    const out = diagnoseConfig(rows, config, emptyResult);
    const f = out.findings.find((x) => x.id === "null_sink");
    expect(f).toBeDefined();
    expect(f?.severity).toBe("medium");
    expect(f?.evidence["column"]).toBe("middle");
  });

  it("reports a clean config as solid (no findings)", () => {
    const rows: Row[] = Array.from({ length: 12 }, (_, i) => ({
      name: `person-${i}`,
    }));
    const config: GoldenMatchConfig = { matchkeys: [weightedOn("name")] };
    const out = diagnoseConfig(rows, config, emptyResult);
    expect(out.findings).toEqual([]);
    expect(out.summary_plain.toLowerCase()).toContain("solid");
  });

  it("returns the Python-parity response shape", () => {
    const rows: Row[] = [{ record_id: "a", name: "x" }];
    const config: GoldenMatchConfig = { matchkeys: [weightedOn("record_id")] };
    const out = diagnoseConfig(rows, config, emptyResult, { phrasing: "technical" });
    expect(Object.keys(out).sort()).toEqual(["findings", "summary_plain"]);
    expect(typeof out.summary_plain).toBe("string");
    for (const f of out.findings) {
      expect(Object.keys(f).sort()).toEqual(
        ["detail_plain", "evidence", "fix_config_hint", "fix_plain", "id", "severity", "title_plain"].sort(),
      );
    }
  });

  it("respects max_findings and ranks high severity first", () => {
    const rows: Row[] = Array.from({ length: 6 }, (_, i) => ({
      record_id: `id-${i}`,
      source: "crm",
      name: i < 3 ? "a" : "b",
    }));
    const config: GoldenMatchConfig = {
      matchkeys: [
        {
          name: "mk",
          type: "weighted",
          threshold: 0.85,
          fields: [
            { field: "record_id", transforms: [], scorer: "exact", weight: 1.0 },
            { field: "source", transforms: [], scorer: "exact", weight: 1.0 },
          ],
        },
      ],
    };
    const out = diagnoseConfig(rows, config, emptyResult, { maxFindings: 1 });
    expect(out.findings.length).toBe(1);
    expect(out.findings[0]!.severity).toBe("high");
  });
});
