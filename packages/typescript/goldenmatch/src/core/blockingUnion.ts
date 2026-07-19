/**
 * blockingUnion.ts — the #1207 strong-identifier blocking union DECISION logic,
 * the TS port of the shared `autoconfig-core` kernel
 * (`autoconfig-core/src/select_blocking.rs`).
 *
 * This is increment 2a of moving blocking-key selection into the shared core: the
 * pure two-phase decision (`assembleStrongIdUnion` + `finalizeStrongIdUnion`),
 * byte-for-byte with the Rust core and pinned to it by the cross-surface golden
 * fixture (`tests/parity/fixtures/select-blocking/`, also checked by the Rust
 * golden test + the wasm shims). Blocking selection is data-dependent, so the
 * split mirrors the core: the host MEASURES the row-level signals (OR-coverage,
 * per-pass block size), these pure functions GATE.
 *
 * **Not yet wired into `buildBlocking`.** The always-on runtime reroute is
 * increment 2b, deliberately deferred: the core's `assemble` derives name-column
 * detection from `classify_by_name` (a name-*pattern*-only classifier — e.g. bare
 * `first`/`last` are NOT names, only `first_name`/`surname` are), which differs
 * from TS's data-aware `classifyColumn`. Feeding TS's classifier makes the union
 * over-fire vs Python (it fired on a bare-`first`/`last` dataset where Python's
 * `_classify_by_name` returns None). Closing that requires the core's
 * `classify_by_name` to be the name-classification authority on the TS surface —
 * a deliberate decision (shared classifier vs a faithful port), not a rush. Until
 * then this ships the shared decision kernel + parity, like fs-wasm shipped its
 * kernel before the scoring-path reroute.
 */

/** `autoconfig.py::_STRONG_EXACT_TYPES` — the strong-identifier col_types. */
const STRONG_EXACT_TYPES = new Set(["identifier", "email", "phone"]);
/** `_UNION_PASS_MIN_NONNULL` — a per-id pass must block more than a handful. */
const UNION_PASS_MIN_NONNULL = 0.02;
/** `_BLOCKING_UNION_COVERAGE_TARGET`. */
export const BLOCKING_UNION_COVERAGE_TARGET = 0.95;

/** One column's profile signals the union decision needs (mirrors the core's
 *  `BlockingColumnInput`). `colType` is the classifier's snake_case verdict; per
 *  the file header, name detection here treats `colType === "name"` as
 *  name-classified — faithful on the golden fixture, but see 2b for the
 *  `classify_by_name` reconciliation the runtime reroute needs. */
export interface UnionColumn {
  readonly name: string;
  readonly colType: string;
  readonly nullRate: number;
  readonly cardinalityRatio: number;
}

/** A candidate pass. `isStrongId` marks the single-field strong-id singletons. */
export interface UnionPass {
  readonly fields: readonly string[];
  readonly transforms: readonly string[];
  readonly isStrongId: boolean;
}

/** The emitted union config shape (mirrors the core's `BlockingConfigOut`). */
export interface UnionConfigOut {
  readonly strategy: "multi_pass";
  readonly keys: readonly UnionPass[];
  readonly passes: readonly UnionPass[];
  readonly maxBlockSize: number;
  readonly skipOversized: true;
}

function isStrong(colType: string): boolean {
  return STRONG_EXACT_TYPES.has(colType);
}

/** `_transforms_for(fields)` — email → `[lowercase, strip]`, else `[strip]`. */
function transformsForField(field: string, cols: readonly UnionColumn[]): string[] {
  const c = cols.find((x) => x.name === field);
  return c && c.colType === "email" ? ["lowercase", "strip"] : ["strip"];
}

/**
 * Phase 1 — assemble the candidate union passes from column profiles (pure).
 * Faithful port of `assemble_strong_id_union`. `null` unless ≥1 strong-id pass
 * AND ≥2 distinct passes survive assembly.
 */
export function assembleStrongIdUnion(
  cols: readonly UnionColumn[],
): UnionPass[] | null {
  const passes: UnionPass[] = [];
  let strongIdCount = 0;

  for (const c of cols) {
    if (!isStrong(c.colType)) continue;
    const nonnull = 1.0 - c.nullRate;
    if (nonnull < UNION_PASS_MIN_NONNULL) continue;
    // #876 surrogate guard: a perfect-surrogate id (card_ratio >= 1.0) makes
    // singleton blocks. blocking_max_ratio is deliberately NOT applied here.
    if (c.cardinalityRatio >= 1.0) continue;
    passes.push({
      fields: [c.name],
      transforms: transformsForField(c.name, cols),
      isStrongId: true,
    });
    strongIdCount += 1;
  }

  if (strongIdCount < 1) return null;

  // name+geo passes for rows missing every strong id.
  const nameCols = cols.filter((c) => c.colType === "name");
  const first = nameCols.find((c) => c.name.toLowerCase().includes("first"))?.name;
  const last = nameCols.find((c) => {
    const n = c.name.toLowerCase();
    return n.includes("last") || n.includes("surname");
  })?.name;
  const geo = cols.find((c) => c.colType === "zip" || c.colType === "geo")?.name;

  if (first !== undefined && last !== undefined) {
    passes.push({
      fields: [first, last],
      transforms: transformsForField(first, cols),
      isStrongId: false,
    });
  }
  if (last !== undefined && geo !== undefined) {
    passes.push({
      fields: [last, geo],
      transforms: transformsForField(last, cols),
      isStrongId: false,
    });
  }

  if (passes.length < 2) return null;
  return passes;
}

/**
 * Phase 2 — apply the gates and emit the `multi_pass` union config (pure).
 * Faithful port of `finalize_strong_id_union`. `null` (fall through) when the
 * coverage target is not cleared, or < 2 passes survive, or no strong-id
 * survives. `passSurvives[i]` is the host's scale-safety verdict for `passes[i]`.
 */
export function finalizeStrongIdUnion(
  passes: readonly UnionPass[],
  coverage: number,
  passSurvives: readonly boolean[],
  maxSafeBlock: number,
): UnionConfigOut | null {
  if (coverage < BLOCKING_UNION_COVERAGE_TARGET) return null;
  if (passSurvives.length !== passes.length) return null;
  const survivors = passes.filter((_, i) => passSurvives[i]);
  const anyStrongId = survivors.some((p) => p.isStrongId);
  if (!anyStrongId || survivors.length < 2) return null;
  return {
    strategy: "multi_pass",
    keys: [survivors[0]!],
    passes: survivors,
    maxBlockSize: maxSafeBlock,
    skipOversized: true,
  };
}
