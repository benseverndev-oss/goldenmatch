/**
 * keyIntegrity.ts — the semantic-layer key-integrity certificate (edge-safe).
 *
 * Edge-safe port of the structural tier of Python
 * `semantic/key_integrity.py::certify_key_integrity` +
 * `core/key_integrity_certificate.py::KeyIntegrityCertificate`.
 *
 * A semantic layer (dbt/MetricFlow, Cube, OSI) is a join graph, and every join
 * runs on entity-key equality. A metric is only correct if the declared key
 * genuinely, uniquely identifies one real entity. `certifyKeyIntegrity` is the
 * advisory answer to "is that declared key trustworthy?": is it unique at grain,
 * and how much would a duplicated key inflate a `SUM`/`COUNT(DISTINCT)` (fan-out)?
 * It never mutates a number; it reports and quantifies.
 *
 * SCOPE: `certifyKeyIntegrity` computes the **structural** tier only (uniqueness
 * + fan-out), which is all the serving-join certificate needs and stays
 * synchronous. The Python `resolve=true` fragmentation/undercount tier — which
 * runs entity resolution on the record's attributes — is ported as the async
 * {@link resolveKeyIntegrity} (ER via `dedupe()` is async in TS). The certificate
 * carries the resolve fields either way (null until a resolve pass runs).
 */

import type { GoldenMatchConfig, Row } from "../types.js";
import { getMatchkeys } from "../types.js";
import { autoConfigureRows } from "../autoconfig.js";
import { dedupe } from "../api.js";

export interface KeyIntegrityCertificateInit {
  keyColumns: string[];
  grain: string[] | null;
  nRows: number;
  nKeyGroups: number;
  isUniqueAtGrain: boolean;
  duplicateKeyGroups: number;
  maxFanOut: number;
  measureFanOut?: Record<string, number>;
  note?: string;
}

/** Advisory certificate for a declared entity key (structural tier). */
export class KeyIntegrityCertificate {
  readonly keyColumns: string[];
  readonly grain: string[] | null;
  readonly nRows: number;
  readonly nKeyGroups: number; // distinct key(-at-grain) tuples
  readonly isUniqueAtGrain: boolean; // nKeyGroups === nRows
  readonly duplicateKeyGroups: number; // key groups with >1 row
  readonly maxFanOut: number; // worst-case row multiplicity for a key group
  readonly measureFanOut: Record<string, number>; // per-measure SUM inflation ratio
  note: string;

  // Resolution tier (opt-in via resolveKeyIntegrity): does ER collapse distinct
  // declared keys? null until a resolve pass populates them (mirrors Python's
  // KeyIntegrityCertificate defaults). Mutated in place by the resolve tier, as
  // the Python dataclass is.
  resolvedEntities: number | null = null; // multi-member clusters found by dedupe
  fragmentedEntities: number | null = null; // resolved entities spanning >1 declared key value
  undercountEstimate: number | null = null; // fragmentedEntities / resolvedEntities
  estimable = true;

  constructor(init: KeyIntegrityCertificateInit) {
    this.keyColumns = init.keyColumns;
    this.grain = init.grain;
    this.nRows = init.nRows;
    this.nKeyGroups = init.nKeyGroups;
    this.isUniqueAtGrain = init.isUniqueAtGrain;
    this.duplicateKeyGroups = init.duplicateKeyGroups;
    this.maxFanOut = init.maxFanOut;
    this.measureFanOut = init.measureFanOut ?? {};
    this.note = init.note ?? "";
  }

  /** Point score in [0,1]: the fraction of key groups that are clean (unique at
   * grain). 1.0 == the declared key is a true key. Mirrors Python `estimate`. */
  get estimate(): number {
    if (this.nKeyGroups === 0) return 1.0;
    return 1.0 - this.duplicateKeyGroups / this.nKeyGroups;
  }

  /** Conservative score: also discounts entity fragmentation when it was
   * measured (a fragmented entity is a silent join defect the structural pass
   * can't see). Collapses to the structural estimate when the resolution tier
   * hasn't run (Python `safe_bound` with `undercount_estimate === null`). */
  get safeBound(): number {
    if (this.undercountEstimate === null) return this.estimate;
    return Math.min(this.estimate, 1.0 - this.undercountEstimate);
  }

  /** Advisory pass/fail: the declared key is unique at grain, doesn't fan out
   * beyond `maxFanOut`, and clears `minEstimate`. Never enforced. */
  isTrustworthy({ maxFanOut = 1.0, minEstimate = 1.0 }: { maxFanOut?: number; minEstimate?: number } = {}): boolean {
    return this.isUniqueAtGrain && this.maxFanOut <= maxFanOut && this.estimate >= minEstimate;
  }
}

function asList(x: string | readonly string[] | undefined): string[] {
  if (x === undefined) return [];
  return typeof x === "string" ? [x] : [...x];
}

/** Stable per-row group key over the group columns. `null`/`undefined` are a
 * distinct group (matching pyarrow group_by), and a JSON encoding keeps a real
 * `null` distinct from the string `"null"`. */
function groupKey(row: readonly unknown[]): string {
  return JSON.stringify(row.map((v) => (v === undefined ? null : v)));
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * Certify a declared entity key for metric use (structural tier). Mirrors Python
 * `certify_key_integrity`'s structural pass.
 *
 * @param table  column-oriented data (`{ column: values[] }`), the edge-safe
 *   analogue of the Arrow table / dict the Python fn takes. Every column must be
 *   the same length.
 * @param opts.key       the declared entity key column(s).
 * @param opts.measures  numeric columns whose SUM fan-out is quantified per key.
 * @param opts.grain     the model's aggregation grain (advisory context by
 *   default; folded into the grouping when `grainStrict`).
 * @param opts.grainStrict  evaluate uniqueness/fan-out on `key + grain`.
 */
export function certifyKeyIntegrity(
  table: Readonly<Record<string, readonly unknown[]>>,
  opts: { key: string | readonly string[]; measures?: readonly string[]; grain?: readonly string[]; grainStrict?: boolean },
): KeyIntegrityCertificate {
  const keyColumns = asList(opts.key);
  const measureCols = asList(opts.measures);
  const grainCols = asList(opts.grain);
  const grainStrict = opts.grainStrict ?? false;

  if (keyColumns.length === 0) {
    throw new Error("certifyKeyIntegrity: `key` must name at least one column");
  }
  const present = new Set(Object.keys(table));
  const missingKeys = keyColumns.filter((c) => !present.has(c));
  if (missingKeys.length) {
    throw new Error(`certifyKeyIntegrity: key column(s) not in table: ${missingKeys.join(", ")}`);
  }
  const missingMeasures = measureCols.filter((c) => !present.has(c));
  if (missingMeasures.length) {
    throw new Error(`certifyKeyIntegrity: measure column(s) not in table: ${missingMeasures.join(", ")}`);
  }
  if (grainStrict && grainCols.length) {
    const missingGrain = grainCols.filter((c) => !present.has(c));
    if (missingGrain.length) {
      throw new Error(`certifyKeyIntegrity: grain column(s) not in table: ${missingGrain.join(", ")}`);
    }
  }

  const firstKeyCol = table[keyColumns[0]!]!;
  const nRows = firstKeyCol.length;
  const notes: string[] = [];

  // numeric measures only (non-numeric skipped for fan-out, like Python)
  const numericMeasures = measureCols.filter((m) => (table[m] ?? []).every((v) => v === null || v === undefined || isFiniteNumber(v)));
  const skipped = measureCols.filter((m) => !numericMeasures.includes(m));
  if (skipped.length) notes.push(`non-numeric measures skipped for fan-out: ${skipped.join(", ")}`);

  const groupColumns =
    grainStrict && grainCols.length ? [...keyColumns, ...grainCols.filter((g) => !keyColumns.includes(g))] : keyColumns;

  // group-by: count rows per key-tuple + track per-measure max within the group
  const counts = new Map<string, number>();
  const measureMax = new Map<string, Record<string, number>>();
  for (let i = 0; i < nRows; i++) {
    const tuple = groupColumns.map((c) => (table[c] ?? [])[i]);
    const gk = groupKey(tuple);
    counts.set(gk, (counts.get(gk) ?? 0) + 1);
    if (numericMeasures.length) {
      const mm = measureMax.get(gk) ?? {};
      for (const m of numericMeasures) {
        const v = (table[m] ?? [])[i];
        if (isFiniteNumber(v)) mm[m] = m in mm ? Math.max(mm[m]!, v) : v;
      }
      measureMax.set(gk, mm);
    }
  }

  const nKeyGroups = counts.size;
  let maxFanOut = 0;
  let duplicateKeyGroups = 0;
  for (const c of counts.values()) {
    if (c > maxFanOut) maxFanOut = c;
    if (c > 1) duplicateKeyGroups += 1;
  }
  const isUniqueAtGrain = nKeyGroups === nRows;

  const measureFanOut: Record<string, number> = {};
  for (const m of numericMeasures) {
    let totalSum = 0;
    for (const v of table[m] ?? []) if (isFiniteNumber(v)) totalSum += v;
    let dedupSum = 0;
    for (const mm of measureMax.values()) if (isFiniteNumber(mm[m])) dedupSum += mm[m]!;
    measureFanOut[m] = dedupSum ? totalSum / dedupSum : 1.0;
  }

  if (grainCols.length) {
    notes.push(
      grainStrict
        ? `uniqueness evaluated at grain: key + ${grainCols.join(", ")}`
        : `grain ${grainCols.join(", ")} recorded as context; uniqueness evaluated on key`,
    );
  }

  return new KeyIntegrityCertificate({
    keyColumns,
    grain: grainCols.length ? grainCols : null,
    nRows,
    nKeyGroups,
    isUniqueAtGrain,
    duplicateKeyGroups,
    maxFanOut: nKeyGroups ? maxFanOut : 0,
    measureFanOut,
    note: notes.join("; "),
  });
}

/** Per-row declared-key tuple, encoded as a stable string (positional; row i ==
 * cluster member id i). `null`/`undefined` normalize together, and the JSON
 * encoding keeps a real `null` distinct from the string `"null"` — mirroring
 * `groupKey` and Python's `_row_key_values` tuple identity. */
function rowKeyValues(table: Readonly<Record<string, readonly unknown[]>>, keyColumns: string[]): string[] {
  const cols = keyColumns.map((c) => table[c] ?? []);
  const n = cols.length ? cols[0]!.length : 0;
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    out.push(groupKey(cols.map((col) => col[i])));
  }
  return out;
}

/**
 * Certify a declared entity key AND run the resolution tier — the async, edge-safe
 * port of Python `certify_key_integrity(..., resolve=True)` (`_add_resolution`).
 *
 * Computes the structural certificate (via {@link certifyKeyIntegrity}), then runs
 * zero-config entity resolution over the record's ATTRIBUTE columns (every column
 * that isn't the key or a measure, unless `attributes` is given) and checks whether
 * any resolved entity (a ≥2-member cluster) spans more than one distinct declared
 * key tuple — fragmentation, i.e. the join undercounts distinct entities. It NEVER
 * mutates the data and is **fail-open**: any resolution failure leaves the resolve
 * fields null, sets `estimable` from the structural pass, and records an explanatory
 * note — the structural certificate is always returned intact.
 *
 * ER is run async here because TS `dedupe()` is async (Python's `dedupe_df` is sync);
 * the returned certificate carries the same `resolvedEntities` / `fragmentedEntities`
 * / `undercountEstimate` / `estimable` contract as the Python dataclass. Attribute
 * selection here is BLIND by default (all non-key, non-measure columns); pass an
 * explicit `attributes` list for the metric-aware, role-driven selection —
 * `certifySemanticModelResolved` computes it from the model's declared dimensions
 * via `blocking.ts` (`metricAwareAttributes`).
 */
export async function resolveKeyIntegrity(
  table: Readonly<Record<string, readonly unknown[]>>,
  opts: {
    key: string | readonly string[];
    measures?: readonly string[];
    grain?: readonly string[];
    grainStrict?: boolean;
    attributes?: readonly string[];
  },
): Promise<KeyIntegrityCertificate> {
  const cert = certifyKeyIntegrity(table, opts);
  const keyColumns = asList(opts.key);
  const measureCols = asList(opts.measures);
  const resNotes = await addResolution(cert, table, keyColumns, measureCols, opts.attributes);
  cert.note = [cert.note, ...resNotes].filter((s) => s.length > 0).join("; ");
  return cert;
}

/** Populate the fragmentation/undercount fields via GoldenMatch ER. Fail-open:
 * any error leaves the resolution fields null and returns an explanatory note.
 * Mirrors Python `_add_resolution`. */
async function addResolution(
  cert: KeyIntegrityCertificate,
  table: Readonly<Record<string, readonly unknown[]>>,
  keyColumns: string[],
  measureCols: string[],
  attributes: readonly string[] | undefined,
): Promise<string[]> {
  const notes: string[] = [];
  const exclude = new Set<string>([...keyColumns, ...measureCols]);
  const attrs =
    attributes !== undefined ? [...attributes] : Object.keys(table).filter((c) => !exclude.has(c));
  if (attrs.length === 0) {
    notes.push("resolution skipped: no attribute columns to resolve on");
    return notes;
  }

  try {
    const nRows = (table[keyColumns[0]!] ?? []).length;
    const subRows: Row[] = [];
    for (let i = 0; i < nRows; i++) {
      const row: Record<string, unknown> = {};
      for (const a of attrs) row[a] = (table[a] ?? [])[i];
      subRows.push(row);
    }

    // Zero-config, then disable rerank/cross-encoder so the certification path
    // never blocks on a model download (offline-safe), mirroring Python.
    const cfg = autoConfigureRows(subRows);
    const matchkeys = getMatchkeys(cfg).map((mk) => ({ ...mk, rerank: false }));
    const subConfig: GoldenMatchConfig = { ...cfg, matchkeys };
    const res = await dedupe(subRows, { config: subConfig });

    const keyvals = rowKeyValues(table, keyColumns);
    let resolved = 0;
    let fragmented = 0;
    for (const cl of res.clusters.values()) {
      const members = cl.members;
      if (members.length < 2) continue;
      resolved += 1;
      const distinctKeys = new Set<string>();
      for (const m of members) distinctKeys.add(keyvals[Math.trunc(m)]!);
      if (distinctKeys.size > 1) fragmented += 1;
    }

    cert.resolvedEntities = resolved;
    cert.fragmentedEntities = fragmented;
    cert.undercountEstimate = resolved ? fragmented / resolved : 0.0;
    if (fragmented) {
      notes.push(
        `${fragmented}/${resolved} resolved entities span >1 declared key ` +
          "(fragmentation → the join undercounts distinct entities)",
      );
    }
  } catch (err) {
    // fail-open: never break the structural certificate
    cert.estimable = cert.isUniqueAtGrain && !cert.duplicateKeyGroups;
    const name = err instanceof Error ? err.constructor.name : "Error";
    const msg = err instanceof Error ? err.message : String(err);
    notes.push(`resolution unavailable (${name}: ${msg})`);
  }
  return notes;
}
