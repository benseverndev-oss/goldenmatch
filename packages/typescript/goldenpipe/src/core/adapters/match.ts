/**
 * GoldenMatch adapter — wraps GoldenMatch-JS `dedupe`.
 * Port of goldenpipe/adapters/match.py (+ `_build_config_from_contexts`).
 *
 * `dedupe` is ASYNC, so this stage's `run` awaits it (and the whole runner is
 * async). Config selection priority mirrors Python:
 *   1. explicit stage config (from YAML / PipelineConfig.config)
 *   2. config built from upstream column contexts
 *   3. GoldenMatch auto-configure (no config → shorthand path)
 *
 * Shape divergences from the Python sibling, surfaced as artifacts:
 *   - GoldenMatch-JS `DedupeResult` exposes `.goldenRecords` (not `.golden`),
 *     `.dupes`, `.unique`, `.stats`, `.scoredPairs`. We map `goldenRecords` to
 *     the `golden` artifact for parity with the Python pipeline's artifact name.
 *   - `matchkey_used` is derived from the built config's first matchkey (the
 *     JS result does not carry the resolved matchkey list back).
 *
 * Edge-safe: no `node:` imports (GoldenMatch-JS core is edge-safe).
 */

import { dedupe, makeConfig, makeMatchkeyConfig, makeMatchkeyField, makeBlockingConfig } from "goldenmatch/core";
import type {
  GoldenMatchConfig,
  MatchkeyConfig,
  MatchkeyField,
  BlockingConfig,
  BlockingKeyConfig,
} from "goldenmatch/core";
import type { PipeContext, Stage, StageResult, Row } from "../models.js";
import { StageStatus } from "../models.js";
import {
  ColumnType,
  distinctNonNull,
  nullRateOf,
  type ColumnContext,
} from "../columnContext.js";

/** Cast every cell to string, mirroring the Python adapter's defensive cast
 *  that prevents mixed-type-column schema mismatches reaching GoldenMatch. */
function castRowsToString(rows: readonly Row[]): Row[] {
  return rows.map((row) => {
    const out: Row = {};
    for (const [k, v] of Object.entries(row)) {
      out[k] = v === null || v === undefined ? "" : String(v);
    }
    return out;
  });
}

export const DedupeStage: Stage = {
  info: { name: "goldenmatch.dedupe", produces: ["clusters", "golden"], consumes: ["df"] },

  validate(ctx: PipeContext): void {
    if (ctx.df === null) {
      throw new Error("DedupeStage: no df in context");
    }
  },

  async run(ctx: PipeContext): Promise<StageResult> {
    const rows = castRowsToString(ctx.df ?? []);
    ctx.df = rows;

    const stageCfg = ctx.stageConfig;
    let config: GoldenMatchConfig | null = null;

    // Priority 1: explicit stage config.
    if (stageCfg && Object.keys(stageCfg).length > 0) {
      config = makeConfig(stageCfg as Partial<GoldenMatchConfig>);
    } else {
      // Priority 2: build config from upstream column contexts.
      const contexts = ctx.artifacts["column_contexts"];
      if (Array.isArray(contexts) && contexts.length > 0) {
        config = buildConfigFromContexts(contexts as ColumnContext[], rows);
      }
    }

    // Priority 3 falls through: no config → dedupe auto-configures.
    const result = config !== null ? await dedupe(rows, { config }) : await dedupe(rows);

    ctx.artifacts["clusters"] = result.clusters;
    ctx.artifacts["golden"] = result.goldenRecords;
    ctx.artifacts["unique"] = result.unique;
    ctx.artifacts["dupes"] = result.dupes;
    ctx.artifacts["match_stats"] = result.stats;
    ctx.artifacts["scored_pairs"] = result.scoredPairs;

    // Surface the first matchkey name (best-effort) for downstream stages.
    const mks = config?.matchkeys;
    if (mks && mks.length > 0) {
      ctx.artifacts["matchkey_used"] = mks[0]!.name;
    }

    return { status: StageStatus.SUCCESS };
  },

  rollback: null,
};

/**
 * Build a GoldenMatchConfig from pipeline column contexts. Returns `null` if no
 * usable matchkeys can be built (caller then falls back to auto-configure).
 * Port of `_build_config_from_contexts`.
 */
export function buildConfigFromContexts(
  contexts: readonly ColumnContext[],
  rows: readonly Row[],
): GoldenMatchConfig | null {
  const nameCols = contexts.filter(
    (c) => c.inferredType === ColumnType.NAME && c.isIdentifier,
  );
  const emailCols = contexts.filter((c) => c.inferredType === ColumnType.EMAIL);
  const geoCols = contexts.filter((c) => c.inferredType === ColumnType.GEO);

  const matchkeys: MatchkeyConfig[] = [];

  // Exact matchkeys for high-quality discriminators (email).
  for (const col of emailCols) {
    matchkeys.push(
      makeMatchkeyConfig({
        name: `exact_${col.name}`,
        type: "exact",
        fields: [makeMatchkeyField({ field: col.name, transforms: ["lowercase", "strip"], scorer: "exact" })],
      }),
    );
  }

  // Fuzzy matchkey on name columns (the core of person matching).
  if (nameCols.length > 0) {
    const fuzzyFields: MatchkeyField[] = nameCols.map((col) =>
      makeMatchkeyField({
        field: col.name,
        scorer: "jaro_winkler",
        weight: 1.0,
        transforms: ["lowercase", "strip"],
      }),
    );
    matchkeys.push(
      makeMatchkeyConfig({
        name: "fuzzy_names",
        type: "weighted",
        threshold: 0.85,
        fields: fuzzyFields,
      }),
    );
  }

  // Fallback: no identifier columns — use discriminative string columns.
  // Exclude low-cardinality columns (they inflate fuzzy scores without
  // providing meaningful discrimination).
  if (matchkeys.length === 0) {
    let stringCols = contexts.filter(
      (c) => c.inferredType === ColumnType.STRING || c.inferredType === ColumnType.NAME,
    );
    if (rows.length > 0) {
      const minCardinality = Math.max(10, Math.floor(rows.length * 0.05));
      stringCols = stringCols.filter((c) => distinctNonNull(rows, c.name) >= minCardinality);
    }
    const fallbackFields: MatchkeyField[] = stringCols.slice(0, 3).map((col) =>
      makeMatchkeyField({
        field: col.name,
        scorer: "jaro_winkler",
        weight: 1.0,
        transforms: ["lowercase", "strip"],
      }),
    );
    if (fallbackFields.length > 0) {
      matchkeys.push(
        makeMatchkeyConfig({
          name: "fuzzy_fallback",
          type: "weighted",
          threshold: 0.85,
          fields: fallbackFields,
        }),
      );
    }
  }

  // No matchkeys → give up; caller falls back to auto-configure.
  if (matchkeys.length === 0) {
    return null;
  }

  // Blocking: compound geo columns with name to prevent cross-region false
  // positives. Prefer low-cardinality geo (state ~50) over high (city ~3000).
  let bestGeo: string | null = null;
  if (geoCols.length > 0 && rows.length > 0) {
    const maxNullRate = 0.2;
    const geoCandidates: Array<[string, number]> = [];
    for (const g of geoCols) {
      if (nullRateOf(rows, g.name) <= maxNullRate) {
        geoCandidates.push([g.name, distinctNonNull(rows, g.name)]);
      }
    }
    if (geoCandidates.length > 0) {
      geoCandidates.sort((a, b) => a[1] - b[1]);
      bestGeo = geoCandidates[0]![0];
    }
  }

  const makeBlocking = (
    primaryFields: string[],
    recallName: string,
    withGeo = false,
  ): BlockingConfig => {
    const passes: BlockingKeyConfig[] = [
      { fields: primaryFields, transforms: ["lowercase", "strip"] },
    ];
    if (withGeo) {
      passes.push({ fields: primaryFields, transforms: ["lowercase", "substring:0:3"] });
    }
    // Name-only soundex recall pass (phonetic variants; relies on skipOversized).
    passes.push({ fields: [recallName], transforms: ["lowercase", "soundex"] });
    return makeBlockingConfig({
      strategy: "multi_pass",
      keys: [passes[0]!],
      passes,
      maxBlockSize: 500,
      skipOversized: true,
    });
  };

  let blocking: BlockingConfig | null = null;
  const lastNameCols = nameCols.filter((c) => c.name.toLowerCase().includes("last"));
  if (lastNameCols.length > 0) {
    const bestName = lastNameCols[0]!.name;
    blocking = bestGeo
      ? makeBlocking([bestGeo, bestName], bestName, true)
      : makeBlocking([bestName], bestName);
  } else if (nameCols.length > 0) {
    const bestName = nameCols[0]!.name;
    if (bestGeo) {
      blocking = makeBlocking([bestGeo, bestName], bestName, true);
    } else {
      blocking = makeBlockingConfig({
        strategy: "static",
        keys: [{ fields: [bestName], transforms: ["lowercase", "soundex"] }],
        maxBlockSize: 500,
        skipOversized: true,
      });
    }
  }

  // Fallback: no name columns, but string matchkeys + geo present.
  if (!blocking && bestGeo && matchkeys.length > 0) {
    const fuzzyMks = matchkeys.filter((mk) => mk.type === "weighted");
    const first = fuzzyMks[0];
    if (first && first.fields.length > 0) {
      const anchor = first.fields[0]!.field;
      blocking = makeBlocking([bestGeo, anchor], anchor, true);
    }
  }

  // Still no blocking → let GoldenMatch auto-suggest.
  if (!blocking) {
    blocking = makeBlockingConfig({ keys: [], autoSuggest: true });
  }

  return makeConfig({ matchkeys, blocking });
}
