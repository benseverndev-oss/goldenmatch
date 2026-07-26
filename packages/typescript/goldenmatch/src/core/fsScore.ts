/**
 * fsScore.ts — the Fellegi-Sunter block-scoring REROUTE adapter.
 *
 * Builds the JSON-boundary `FsBlockScoringInput` the shared `fs-core` wasm
 * kernel takes from the SAME `(rows, matchkey, EMResult)` the pure-TS
 * `scoreProbabilistic` consumes, then calls `scoreBlockPairsFs`. This is the TS
 * mirror of the Python-native input construction in
 * `goldenmatch/core/probabilistic.py::_score_fs_native_frame` — so TS FS block
 * scoring becomes byte-aligned with Python-native (same kernel, same inputs).
 *
 * THE F1-MOVING DELTA vs pure-TS `scoreProbabilistic`: normalization uses the
 * FIXED full-field weight range (`fsWeightRange`, the #1854 fix Python ships),
 * NOT the pure-TS per-pair SHRINKING range (only-observed-fields). This is the
 * intended alignment onto Python's operating point — see PR "fs-default-ts-path".
 *
 * HEAVY module (imports the inlined `fs-wasm` kernel) — a separate tsup subpath
 * (`goldenmatch/core/fs-scoring`) so the ~187 KB wasm never enters the default
 * `core` bundle. `pipeline.ts` reaches this only through the lean
 * `fsScoreBackend` registry, which importing this module + calling
 * `enableFsWasmScoring()` populates.
 */
import type {
  Row,
  MatchkeyConfig,
  MatchkeyField,
  NegativeEvidenceField,
  ScoredPair,
} from "./types.js";
import { makeScoredPair } from "./types.js";
import { asString } from "./scorer.js";
import { applyTransforms } from "./transforms.js";
import { fsWeightRange } from "./probabilistic.js";
import type { EMResult } from "./probabilistic.js";
import { scoreBlockPairsFs } from "./fsWasm.js";
import type { FsBlockScoringInput } from "./fsWasm.js";
import {
  setFsScoreBackend,
  disableFsWasmScoring,
} from "./fsScoreBackend.js";

// ---------------------------------------------------------------------------
// Scorer-name -> `fs_core` kernel id (mirrors the Python
// `_NATIVE_FS_SCORER_IDS` subset the TS kernel can express). score_one ids
// 0..=3 + ensemble (6). The reference-data name scorers (4/5) need the process
// registered census/alias tables and embedding (7) needs marshaled vectors —
// neither is wired on the TS surface, so a field/NE using them DECLINES the
// reroute to the pure-TS fallback (exactly like Python declines to numpy).
// ---------------------------------------------------------------------------
const FS_SCORER_IDS: Readonly<Record<string, number>> = {
  jaro_winkler: 0,
  levenshtein: 1,
  token_sort: 2,
  exact: 3,
  ensemble: 6,
};

/** Python-parity default `partial_threshold` (schemas.py MatchkeyField). */
const DEFAULT_PARTIAL_THRESHOLD = 0.8;

function fieldLevelCount(f: MatchkeyField): number {
  if (f.levelThresholds !== undefined) return f.levelThresholds.length + 1;
  return f.levels ?? 2;
}

/**
 * The post-transform value column for one field over the block, `null` where the
 * value is missing. Mirrors Python `_field_values_for_block` / the pure-TS
 * `buildComparisonVector` operand computation (`asString` -> `applyTransforms`),
 * so the kernel bands the identical similarity the pure-TS path would.
 */
function fieldValueColumn(
  blockRows: readonly Row[],
  field: string,
  transforms: readonly string[],
): (string | null)[] {
  return blockRows.map((row) => {
    let v = asString(row[field]);
    if (v !== null && transforms.length > 0) v = applyTransforms(v, transforms);
    return v;
  });
}

/**
 * Whether `mk` can be scored by the shared `fs_core` kernel on the TS surface.
 * Mirrors the field/NE-scorer + TF checks of Python `_fs_native_eligible`
 * (minus the wheel-capability probes — the committed fs-wasm always carries the
 * NE + level-banding entry). A non-probabilistic matchkey, or any field/NE using
 * a scorer the TS kernel can't express, or any `tfAdjustment` field, declines.
 */
export function fsRerouteEligible(mk: MatchkeyConfig): boolean {
  if (mk.type !== "probabilistic") return false;
  if (mk.fields.length === 0) return false;
  for (const f of mk.fields) {
    if (!(f.scorer in FS_SCORER_IDS)) return false;
    if (f.tfAdjustment) return false;
  }
  for (const ne of mk.negativeEvidence ?? []) {
    if (!(ne.scorer in FS_SCORER_IDS)) return false;
  }
  return true;
}

/**
 * Build the `FsBlockScoringInput` for a single block from the SAME inputs
 * `scoreProbabilistic` takes. Caller guarantees `fsRerouteEligible(mk)`.
 * `threshold` is the emit cutoff (review/link threshold). Normalization scalars
 * come from the FIXED-range `fsWeightRange` (Python operating point).
 */
export function buildFsBlockScoringInput(
  blockRows: readonly Row[],
  mk: Extract<MatchkeyConfig, { type: "probabilistic" }>,
  em: EMResult,
  threshold: number,
): FsBlockScoringInput {
  const fields = mk.fields;
  const rowIds: number[] = [];
  for (const row of blockRows) {
    const id = row["__row_id__"];
    rowIds.push(typeof id === "number" ? id : Number(id));
  }

  const fieldValues = fields.map((f) =>
    fieldValueColumn(blockRows, f.field, f.transforms),
  );
  const scorerIds = fields.map((f) => FS_SCORER_IDS[f.scorer]!);
  const levels = fields.map((f) => fieldLevelCount(f));
  const partialThresholds = fields.map(
    (f) => f.partialThreshold ?? DEFAULT_PARTIAL_THRESHOLD,
  );
  const matchWeights = fields.map((f) => [...(em.matchWeights[f.field] ?? [])]);

  // Custom banding: one entry per regular field; null => default banding.
  const levelThresholds = fields.map((f) =>
    f.levelThresholds !== undefined ? [...f.levelThresholds] : null,
  );
  const hasBanding = levelThresholds.some((t) => t !== null);

  // FIXED full-field weight range (the #1854 alignment) — NOT the per-pair
  // shrinking range the pure-TS scorer uses.
  const { minWeight, maxWeight } = fsWeightRange(em, mk);

  // Negative evidence (mirrors Python's ne_* opt_kwargs construction). w_fired:
  // -abs(penaltyBits) override, else the EM-learned __ne__<field> fired weight.
  const neFields: readonly NegativeEvidenceField[] = mk.negativeEvidence ?? [];
  const neValues = neFields.map((ne) =>
    fieldValueColumn(blockRows, ne.field, ne.transforms),
  );
  const neScorerIds = neFields.map((ne) => FS_SCORER_IDS[ne.scorer]!);
  const neThresholds = neFields.map((ne) => ne.threshold);
  const neWeights = neFields.map((ne) =>
    ne.penaltyBits !== undefined
      ? -Math.abs(ne.penaltyBits)
      : em.matchWeights[`__ne__${ne.field}`]![0]!,
  );

  const input: FsBlockScoringInput = {
    rowIds,
    blockSizes: [blockRows.length],
    fieldValues,
    scorerIds,
    levels,
    partialThresholds,
    matchWeights,
    calibrated: false, // linear normalization (Python default; posterior stays host-side)
    priorW: 0.0,
    minWeight,
    weightRange: maxWeight - minWeight,
    threshold,
    ...(hasBanding ? { levelThresholds } : {}),
    ...(neFields.length > 0
      ? { neValues, neScorerIds, neThresholds, neWeights }
      : {}),
  };
  return input;
}

/**
 * Score one block via the shared `fs_core` wasm kernel and return the pairs
 * at/above `threshold` as `ScoredPair`s (scores rounded to 4dp — the same
 * convention Python-native and the pure-TS scorer use). Caller guarantees
 * `fsRerouteEligible(mk)`.
 */
export function scoreProbabilisticFsBlock(
  blockRows: readonly Row[],
  mk: MatchkeyConfig,
  em: EMResult,
  threshold: number,
): ScoredPair[] {
  if (mk.type !== "probabilistic") return [];
  if (mk.fields.length === 0 || blockRows.length < 2) return [];
  const input = buildFsBlockScoringInput(blockRows, mk, em, threshold);
  const pairs = scoreBlockPairsFs(input);
  return pairs.map(([a, b, s]) =>
    makeScoredPair(a, b, Math.round(s * 10000) / 10000),
  );
}

/**
 * Register the FS wasm reroute so `scoreProbabilisticBlocks` (pipeline.ts) runs
 * the shared kernel for kernel-expressible probabilistic matchkeys. Idempotent.
 * Mirrors `enableClusterWasm()` / `enableWasm()`.
 */
export function enableFsWasmScoring(): void {
  setFsScoreBackend({
    eligible: fsRerouteEligible,
    scoreBlock: scoreProbabilisticFsBlock,
  });
}

export { disableFsWasmScoring };
