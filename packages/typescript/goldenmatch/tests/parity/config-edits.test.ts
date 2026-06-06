/**
 * config-edits.test.ts -- cross-language parity for the ConfigEdit vocabulary.
 *
 * Replays the Python-emitted edit specs (scripts/emit_config_edits_fixture.py)
 * through the TS port and asserts: identical labels, identical applied/skip
 * decisions, and identical semantic projections of the resulting configs
 * (thresholds / types / scorers / weights / blocking).
 */
import { describe, it, expect } from "vitest";
import { editFromSpec, foldEdits, type ConfigEdit } from "../../src/core/config-edits.js";
import { getMatchkeys } from "../../src/core/types.js";
import type { GoldenMatchConfig } from "../../src/core/types.js";
import fixture from "./fixtures/config-edits.json" with { type: "json" };

function baseConfig(): GoldenMatchConfig {
  return {
    matchkeys: [
      {
        name: "identity",
        type: "weighted",
        threshold: 0.85,
        fields: [
          { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
          { field: "email", transforms: [], scorer: "jaro_winkler", weight: 0.8 },
        ],
      },
      {
        name: "email_exact",
        type: "exact",
        // scorer/weight are required in TS but excluded from the parity
        // projection for exact matchkeys (Python carries None here).
        fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
      },
    ],
    blocking: {
      strategy: "static",
      keys: [
        { fields: ["email"], transforms: ["lowercase"] },
        { fields: ["zip"], transforms: [] },
      ],
      maxBlockSize: 1000,
      skipOversized: true,
    },
  };
}

interface Projection {
  thresholds: Record<string, number | null>;
  types: Record<string, string>;
  scorers: Record<string, string>;
  weights: Record<string, number | null>;
  blocking_strategy: string | null;
  blocking_keys: string[];
}

function project(cfg: GoldenMatchConfig): Projection {
  const thresholds: Record<string, number | null> = {};
  const types: Record<string, string> = {};
  const scorers: Record<string, string> = {};
  const weights: Record<string, number | null> = {};
  for (const mk of getMatchkeys(cfg)) {
    thresholds[mk.name] = (mk as { threshold?: number }).threshold ?? null;
    types[mk.name] = mk.type;
    if (mk.type === "weighted" || mk.type === "probabilistic") {
      for (const f of mk.fields) {
        scorers[`${mk.name}.${f.field}`] = f.scorer;
        weights[`${mk.name}.${f.field}`] = f.weight ?? null;
      }
    }
  }
  const blockingKeys = (cfg.blocking?.keys ?? [])
    .map((k) => k.fields.join("+") + "|" + (k.transforms ?? []).join(","))
    .sort();
  return {
    thresholds,
    types,
    scorers,
    weights,
    blocking_strategy: cfg.blocking?.strategy ?? null,
    blocking_keys: blockingKeys,
  };
}

interface FixtureCase {
  spec: Record<string, unknown>;
  label: string;
  applied: boolean;
  projection: Projection | null;
}

const cases = fixture.cases as unknown as FixtureCase[];

describe("config-edits parity (Python fixture)", () => {
  it("base projection matches Python's base config", () => {
    expect(project(baseConfig())).toEqual(fixture.base_projection);
  });

  it.each(cases.map((c, i) => [i, c.label] as const))(
    "case %i (%s) matches Python",
    (i) => {
      const c = cases[i]!;
      const edit = editFromSpec(c.spec);
      expect(edit, `spec should parse: ${JSON.stringify(c.spec)}`).not.toBeNull();
      expect(edit!.label).toBe(c.label);
      const result = edit!.apply(baseConfig());
      expect(result !== null).toBe(c.applied);
      if (c.applied) {
        expect(project(result!)).toEqual(c.projection);
      }
    },
  );

  it("foldEdits matches Python fold_edits", () => {
    const specs = (fixture.fold_case as { specs: unknown[] }).specs;
    const edits = specs
      .map(editFromSpec)
      .filter((e): e is ConfigEdit => e !== null);
    const folded = foldEdits(baseConfig(), edits);
    expect(project(folded)).toEqual((fixture.fold_case as { projection: Projection }).projection);
  });
});
