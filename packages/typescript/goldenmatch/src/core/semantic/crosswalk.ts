/**
 * ResolvedCrosswalk — the stats-only stand-in the catalog emitters read.
 *
 * The Python `ResolvedCrosswalk` (semantic/crosswalk.py) carries the full
 * `{source, source_pk, resolved_entity_id}` table produced by a dedupe run. The
 * dialect emitters (metricflow / cube / osi), however, read ONLY the provenance
 * stats off it — `source`, `source_pk_column`, `resolved_key`, `n_records`,
 * `n_entities`, `reduction_ratio` — never the row data. That is exactly what
 * `emitSemanticModelFromStore` needs: a stats-only crosswalk built from the live
 * IdentityStore summary. So the TS port models the stats surface, matching the
 * Python `emit_semantic_model_from_store` stand-in (which itself passes an empty
 * table). Running a full dedupe to produce the row table is Python-only (it needs
 * the whole dedupe pipeline); the TS surface regenerates the catalog from the
 * durable control plane, which is the "keep the join live" use case.
 */

/**
 * Round a non-negative value to 6 decimals with Python's round-half-to-even, so
 * `round(reduction_ratio, 6)` matches. `reduction_ratio` is in `[0, 1]`, so the
 * non-negative assumption always holds here.
 */
export function pyRound6(x: number): number {
  const f = 1e6;
  const scaled = x * f;
  const floor = Math.floor(scaled);
  const frac = scaled - floor;
  let n: number;
  if (Math.abs(frac - 0.5) < 1e-9) {
    n = floor % 2 === 0 ? floor : floor + 1; // exact half -> round to even
  } else {
    n = Math.round(scaled);
  }
  return n / f;
}

/** Init for a stats-only `ResolvedCrosswalk` (the emitters' whole read surface). */
export interface ResolvedCrosswalkInit {
  source: string;
  sourcePkColumn: string;
  resolvedKey?: string;
  nRecords?: number;
  nEntities?: number;
  storePath?: string | null;
  note?: string;
}

/**
 * Provenance stats for a resolved-entity crosswalk. Faithful to the fields the
 * Python dialect emitters read off `ResolvedCrosswalk`. `resolvedKey` is the
 * durable control-plane entity id column every metric should join on.
 */
export class ResolvedCrosswalk {
  readonly source: string;
  readonly sourcePkColumn: string;
  readonly resolvedKey: string;
  readonly nRecords: number;
  readonly nEntities: number;
  readonly storePath: string | null;
  readonly note: string;

  constructor(init: ResolvedCrosswalkInit) {
    this.source = init.source;
    this.sourcePkColumn = init.sourcePkColumn;
    this.resolvedKey = init.resolvedKey ?? "resolved_entity_id";
    this.nRecords = init.nRecords ?? 0;
    this.nEntities = init.nEntities ?? 0;
    this.storePath = init.storePath ?? null;
    this.note = init.note ?? "";
  }

  /**
   * `1 - entities/records`: how much the resolved key collapses the source key
   * space (0 = every source_pk is already its own entity). Mirrors the Python
   * `reduction_ratio` property, including the `n_records == 0 -> 0.0` guard.
   */
  get reductionRatio(): number {
    if (!this.nRecords) return 0.0;
    return 1.0 - this.nEntities / this.nRecords;
  }
}
