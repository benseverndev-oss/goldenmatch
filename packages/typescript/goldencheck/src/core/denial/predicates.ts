/**
 * Column encoding + the bounded predicate space for denial-constraint discovery.
 * Parity port of `packages/python/goldencheck/goldencheck/denial/predicates.py`.
 *
 * The evidence/discovery engine works on integer *ids*, never raw values. This
 * module turns a `TabularData` frame into per-column `EncodedColumn` objects and
 * enumerates the Stage-1 predicate space (const / single-tuple / cross-tuple),
 * honouring two load-bearing invariants:
 *
 * - **Order preservation.** `<` / `>` on numeric columns get an order-preserving
 *   dense rank, shared across all same-dtype columns so a cross-column `t.A < t.B`
 *   compares ids in one order space.
 * - **Null handling.** `0` is the null sentinel id. Any predicate whose operand is
 *   null on the relevant row is NOT satisfied; `predicateHolds` checks the null
 *   mask before comparing ids, so the sentinel is never an operand.
 *
 * DIVERGENCE FROM PYTHON (documented, acceptable — the parity gate checks command
 * existence, not date-column output): Python classifies columns from polars
 * dtypes and gets a "temporal" kind for Date/Datetime columns with order-preserving
 * ranks. TS reads dates as strings (the CSV reader / `TabularData` never parses
 * them to a temporal type), so date columns classify as "categorical" (equality
 * only, no `<`/`>`). Null semantics follow Python's `v is None` (a strict `=== null`
 * check, NOT `isNullish`) so id domains match on clean data.
 */

import type { ColumnValue, TabularData } from "../data.js";
import { Op, Predicate, type PredicateKind } from "./models.js";
import { MAX_LITERAL_CARD, MAX_PREDICATES, MIN_SUPPORT } from "./constants.js";

const CAT_OPS: readonly Op[] = [Op.EQ, Op.NE];
const ORD_OPS: readonly Op[] = [Op.EQ, Op.NE, Op.LT, Op.LE, Op.GT, Op.GE];

export type ColumnKind = "categorical" | "numeric";

/** One column encoded to dense integer ids (null → 0). */
export interface EncodedColumn {
  readonly name: string;
  readonly kind: ColumnKind;
  /** Concrete dtype key; ids/pairing are shared only within one exact dtype. */
  readonly dtype: string;
  readonly ids: readonly number[];
  readonly nulls: readonly boolean[];
  readonly card: number;
  readonly idOfValue: ReadonlyMap<ColumnValue, number>;
}

export interface PredicateSpace {
  readonly predicates: readonly Predicate[];
  readonly nSingle: number;
  readonly nCross: number;
  readonly pass2Effective: number;
  readonly capped: boolean;
  readonly enc: ReadonlyMap<string, EncodedColumn>;
}

/**
 * Classify a column from its non-null JS values (Python `_classify` uses polars
 * dtypes). Returns `{kind, dtype}` or `null` for unsupported/mixed columns (omit).
 */
function classify(values: readonly (ColumnValue | null)[]): { kind: ColumnKind; dtype: string } | null {
  let allBoolean = true;
  let allNumber = true;
  let allString = true;
  let anyNonNull = false;
  let hasFloat = false;
  for (const v of values) {
    if (v === null) continue;
    anyNonNull = true;
    if (typeof v !== "boolean") allBoolean = false;
    if (typeof v !== "number") allNumber = false;
    else if (!Number.isInteger(v)) hasFloat = true;
    if (typeof v !== "string") allString = false;
  }
  if (!anyNonNull) return null; // all-null → unsupported (like Polars omit)
  if (allBoolean) return { kind: "categorical", dtype: "boolean" };
  if (allNumber) return { kind: "numeric", dtype: hasFloat ? "float" : "int" };
  if (allString) return { kind: "categorical", dtype: "string" };
  return null; // mixed/other → skip
}

/**
 * Shared value→id map for one concrete dtype's columns.
 * `ordered` → order-preserving dense rank over the sorted distinct values (numeric
 * sort); otherwise first-seen ids in (column, row) order. null is never a member.
 */
function buildDomain(
  members: readonly string[],
  valuesByCol: ReadonlyMap<string, readonly (ColumnValue | null)[]>,
  ordered: boolean,
): Map<ColumnValue, number> {
  if (ordered) {
    const pool = new Set<ColumnValue>();
    for (const name of members) {
      for (const v of valuesByCol.get(name)!) {
        if (v !== null) pool.add(v);
      }
    }
    // Numeric sort — the ordered domain is only ever built for numeric columns.
    const sorted = [...pool].sort((a, b) => (a as number) - (b as number));
    const out = new Map<ColumnValue, number>();
    sorted.forEach((v, i) => out.set(v, i + 1));
    return out;
  }
  const mapping = new Map<ColumnValue, number>();
  let nxt = 1;
  for (const name of members) {
    for (const v of valuesByCol.get(name)!) {
      if (v === null) continue;
      if (!mapping.has(v)) mapping.set(v, nxt++);
    }
  }
  return mapping;
}

/** Encode every supported column to dense ids; unsupported dtypes are omitted. */
export function encodeColumns(df: TabularData): Map<string, EncodedColumn> {
  const kinds = new Map<string, ColumnKind>();
  const dtypes = new Map<string, string>();
  const values = new Map<string, readonly (ColumnValue | null)[]>();

  for (const name of df.columns) {
    const vals = df.column(name);
    const cls = classify(vals);
    if (cls === null) continue;
    kinds.set(name, cls.kind);
    dtypes.set(name, cls.dtype);
    values.set(name, vals);
  }

  // One shared id domain PER CONCRETE DTYPE.
  const idMaps = new Map<string, Map<ColumnValue, number>>();
  for (const [name, kind] of kinds) {
    const key = dtypes.get(name)!;
    if (idMaps.has(key)) continue;
    const members = [...dtypes.keys()].filter((n) => dtypes.get(n) === key);
    idMaps.set(key, buildDomain(members, values, kind !== "categorical"));
  }

  const out = new Map<string, EncodedColumn>();
  for (const [name, kind] of kinds) {
    const idmap = idMaps.get(dtypes.get(name)!)!;
    const vals = values.get(name)!;
    const ids = vals.map((v) => (v === null ? 0 : idmap.get(v)!));
    const nulls = vals.map((v) => v === null);
    const distinct = new Set<ColumnValue>();
    for (const v of vals) if (v !== null) distinct.add(v);
    const idOfValue = new Map<ColumnValue, number>();
    for (const v of distinct) idOfValue.set(v, idmap.get(v)!);
    out.set(name, {
      name,
      kind,
      dtype: dtypes.get(name)!,
      ids,
      nulls,
      card: distinct.size,
      idOfValue,
    });
  }
  return out;
}

function cmp(a: number, op: Op, b: number): boolean {
  switch (op) {
    case Op.EQ:
      return a === b;
    case Op.NE:
      return a !== b;
    case Op.LT:
      return a < b;
    case Op.LE:
      return a <= b;
    case Op.GT:
      return a > b;
    case Op.GE:
      return a >= b;
  }
}

/** Evaluate one predicate. A null operand on the relevant row → NOT satisfied. */
export function predicateHolds(
  p: Predicate,
  enc: ReadonlyMap<string, EncodedColumn>,
  rowA: number,
  rowB: number | null,
): boolean {
  const ea = enc.get(p.colA)!;
  if (ea.nulls[rowA]) return false;

  if (p.kind === "const") {
    const litId = ea.idOfValue.get(p.literal as ColumnValue);
    if (litId === undefined) return false; // literal never appears
    return cmp(ea.ids[rowA]!, p.op, litId);
  }

  const eb = enc.get(p.colB!)!;
  if (p.kind === "single") {
    if (eb.nulls[rowA]) return false;
    return cmp(ea.ids[rowA]!, p.op, eb.ids[rowA]!);
  }

  // cross: tα.A op tβ.B
  const rb = rowB === null ? rowA : rowB;
  if (eb.nulls[rb]) return false;
  return cmp(ea.ids[rowA]!, p.op, eb.ids[rb]!);
}

function opsFor(kind: ColumnKind): readonly Op[] {
  return kind === "categorical" ? CAT_OPS : ORD_OPS;
}

function comparisonSupport(ea: EncodedColumn, eb: EncodedColumn, op: Op, nRows: number): number {
  if (nRows === 0) return 0.0;
  let hits = 0;
  for (let i = 0; i < nRows; i++) {
    if (ea.nulls[i] || eb.nulls[i]) continue;
    if (cmp(ea.ids[i]!, op, eb.ids[i]!)) hits++;
  }
  return hits / nRows;
}

/**
 * Enumerate the bounded Stage-1 predicate space over `df`. Trims by descending
 * support until both the Pass-1 (`nSingle`) and Pass-2 (`2*nSingle + nCross`) mask
 * budgets fit in `MAX_PREDICATES`, flagging `capped`.
 */
export function buildPredicateSpace(df: TabularData): PredicateSpace {
  const enc = encodeColumns(df);
  const names = df.columns.filter((n) => enc.has(n));
  const nRows = df.rowCount;

  const constPreds: Predicate[] = [];
  const constSupport = new Map<Predicate, number>();
  for (const name of names) {
    const ec = enc.get(name)!;
    if (ec.card === 0 || ec.card > MAX_LITERAL_CARD) continue;
    let nNonNull = 0;
    for (const isNull of ec.nulls) if (!isNull) nNonNull++;
    if (nNonNull === 0) continue;
    const counts = new Map<number, number>();
    for (let i = 0; i < ec.nulls.length; i++) {
      if (ec.nulls[i]) continue;
      const id = ec.ids[i]!;
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    const idToValue = new Map<number, ColumnValue>();
    for (const [v, i] of ec.idOfValue) idToValue.set(i, v);
    for (const [vid, cnt] of counts) {
      const support = cnt / nNonNull;
      if (support < MIN_SUPPORT) continue;
      const p = new Predicate("const", name, Op.EQ, null, idToValue.get(vid)!);
      constPreds.push(p);
      constSupport.set(p, support);
    }
  }

  const single: Predicate[] = [];
  const cross: Predicate[] = [];
  for (let i = 0; i < names.length; i++) {
    const a = names[i]!;
    // A op A cross-column (same column, two tuples).
    for (const op of opsFor(enc.get(a)!.kind)) {
      cross.push(new Predicate("cross", a, op, a, null));
    }
    for (let k = i + 1; k < names.length; k++) {
      const b = names[k]!;
      if (enc.get(a)!.dtype !== enc.get(b)!.dtype) continue; // exact same dtype only
      for (const op of opsFor(enc.get(a)!.kind)) {
        single.push(new Predicate("single", a, op, b, null));
        cross.push(new Predicate("cross", a, op, b, null));
      }
    }
  }

  const predicates = [...constPreds, ...single, ...cross];
  const nSingle = constPreds.length + single.length;
  const nCross = cross.length;
  const pass2 = 2 * nSingle + nCross;

  if (nSingle <= MAX_PREDICATES && pass2 <= MAX_PREDICATES) {
    return {
      predicates,
      nSingle,
      nCross,
      pass2Effective: pass2,
      capped: false,
      enc,
    };
  }

  // Over budget: keep highest-support predicates first until BOTH passes fit.
  const score = (p: Predicate): number => {
    if (p.kind === "const") return constSupport.get(p)!;
    return comparisonSupport(enc.get(p.colA)!, enc.get(p.colB!)!, p.op, nRows);
  };
  // Stable descending sort: sort by (-score, original index) to match Python's
  // `sorted(..., reverse=True)` (stable — equal scores keep original order).
  const ranked = predicates
    .map((p, idx) => ({ p, s: score(p), idx }))
    .sort((x, y) => (y.s !== x.s ? y.s - x.s : x.idx - y.idx))
    .map((e) => e.p);

  const kept: Predicate[] = [];
  let ns = 0;
  let nc = 0;
  for (const p of ranked) {
    const isSingle = p.kind === "const" || p.kind === "single";
    const candNs = ns + (isSingle ? 1 : 0);
    const candNc = nc + (isSingle ? 0 : 1);
    if (candNs <= MAX_PREDICATES && 2 * candNs + candNc <= MAX_PREDICATES) {
      kept.push(p);
      ns = candNs;
      nc = candNc;
    }
  }

  return {
    predicates: kept,
    nSingle: ns,
    nCross: nc,
    pass2Effective: 2 * ns + nc,
    capped: true,
    enc,
  };
}

/** Predicate `kind` → kernel kind code (const/single → singles, cross). */
export const KIND_CODE: Readonly<Record<PredicateKind, number>> = { const: 0, single: 1, cross: 2 };
/** `Op` → kernel op code (must match the `cmp` order below). */
export const OP_CODE: Readonly<Record<Op, number>> = {
  [Op.EQ]: 0,
  [Op.NE]: 1,
  [Op.LT]: 2,
  [Op.LE]: 3,
  [Op.GT]: 4,
  [Op.GE]: 5,
};
