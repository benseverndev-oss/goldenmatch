/**
 * InferMap -> Identity Graph bridge (node-only).
 *
 * Helper that writes InferMap's schema-mapping output into the GoldenMatch
 * Identity Graph as `IdentityAlias` rows. When InferMap discovers that
 * `crm.cust_id` maps to `customer_id` (the canonical entity-id field on the
 * target schema), each source record's `crm.cust_id` value becomes an alias
 * that resolves to that record's identity.
 *
 * This is **per-record** alias writing -- InferMap tells us *which column
 * holds the id of this kind*, we record one row per (record, alias-kind).
 * Schema-level "this column maps to that column" aliasing without a record
 * context is intentionally **not** modeled -- the alias table is keyed on the
 * alias *value*, not the column name.
 *
 * Node-only: callers pass a goldenmatch Identity Graph `IdentityStore`. The
 * edge-safe core never imports this module. To keep the published package free
 * of a goldenmatch runtime dependency, the store/alias shapes are modelled by
 * the local {@link AliasStore} / {@link IdentityAliasRow} structural
 * interfaces; a real goldenmatch `IdentityStore` satisfies `AliasStore`.
 *
 * TS parity with `packages/python/infermap/infermap/identity.py`. The Python
 * sibling lazily imports `goldenmatch.identity`; here the caller supplies the
 * store, so there is no import to fail. `store.addAlias` may be async, so this
 * function is async too and the `entityIdResolver` may return a value or a
 * Promise.
 *
 * See issue #206 for the design discussion.
 */

import type { MapResult } from "../core/types.js";

/**
 * Minimal structural shape of a goldenmatch `IdentityAlias` row. Declared
 * locally so the *published* infermap package takes no dependency on
 * goldenmatch — callers pass a real goldenmatch `IdentityStore`, which is
 * structurally compatible. (The integration test exercises the real store
 * via a dev-only dependency.)
 */
export interface IdentityAliasRow {
  alias: string;
  entityId: string;
  kind: string;
  dataset?: string | null;
  recordedAt?: Date;
}

/**
 * Minimal structural shape of a goldenmatch `IdentityStore`. Only `addAlias`
 * is used by this bridge; a real `IdentityStore` satisfies it.
 */
export interface AliasStore {
  addAlias(alias: IdentityAliasRow): unknown | Promise<unknown>;
}

/** Summary of one `writeAliasesFromMapping` invocation. */
export interface AliasWriteResult {
  aliasesWritten: number;
  recordsProcessed: number;
  mappingsUsed: number;
  skippedLowConfidence: number;
  skippedNoValue: number;
  skippedNoEntity: number;
  skippedNoKind: number;
}

/** Serialize an AliasWriteResult to the Python `as_dict()` snake_case shape. */
export function aliasWriteResultAsDict(
  r: AliasWriteResult,
): Record<string, number> {
  return {
    aliases_written: r.aliasesWritten,
    records_processed: r.recordsProcessed,
    mappings_used: r.mappingsUsed,
    skipped_low_confidence: r.skippedLowConfidence,
    skipped_no_value: r.skippedNoValue,
    skipped_no_entity: r.skippedNoEntity,
    skipped_no_kind: r.skippedNoKind,
  };
}

/**
 * Target field names that count as alias-kinds by default. Mirrors the Python
 * `alias_kinds` default. Pass an extended set when your target schema has
 * domain-specific ids (e.g. `"npi"` for healthcare).
 */
export const DEFAULT_ALIAS_KINDS: ReadonlySet<string> = new Set([
  "customer_id",
  "user_id",
  "account_id",
  "external_id",
  "email",
  "phone",
  "ssn",
  "tax_id",
  "ein",
  "vin",
  "isbn",
  "doi",
]);

/** Default minimum confidence -- matches InferMap's "strong match" threshold. */
export const DEFAULT_MIN_CONFIDENCE = 0.85;

/**
 * Return the alias `kind` for a target field name, or null. Strict
 * case-insensitive match against `aliasKinds`. Mirrors Python `_is_alias_kind`.
 */
function isAliasKind(
  targetField: string,
  aliasKinds: ReadonlySet<string>,
): string | null {
  const norm = targetField.toLowerCase().trim();
  return aliasKinds.has(norm) ? norm : null;
}

export type EntityIdResolver = (
  record: Record<string, unknown>,
) => string | null | undefined | Promise<string | null | undefined>;

export interface WriteAliasesOptions {
  /** Source name (e.g. `"crm"`). Namespaces the alias value (`source:value`). */
  sourceName: string;
  /**
   * Target field names that count as alias-kinds. Defaults to
   * {@link DEFAULT_ALIAS_KINDS}.
   */
  aliasKinds?: ReadonlySet<string> | Iterable<string>;
  /** Drop any mapping below this confidence. Defaults to 0.85. */
  minConfidence?: number;
  /** Optional dataset name flowed onto each `IdentityAlias` row. */
  dataset?: string | null;
  /**
   * Logger invoked when a single `addAlias` call throws. Defaults to
   * `console.warn`. A bad row never aborts the batch (identity is additive).
   */
  onError?: (info: {
    alias: string;
    kind: string;
    entityId: string;
    error: unknown;
  }) => void;
}

/**
 * Write `IdentityAlias` rows for each record where InferMap mapped a source
 * column to a known alias-kind target column.
 *
 * @param mapping InferMap's `MapResult` (e.g. from `map(source, target)`).
 * @param records Iterable of source records (dicts keyed by source field name).
 * @param store A goldenmatch `IdentityStore` instance.
 * @param entityIdResolver `record -> entityId | null` (may be async). Returning
 *   null/undefined skips alias writing for that row.
 * @param options See {@link WriteAliasesOptions}; `sourceName` is required.
 */
export async function writeAliasesFromMapping(
  mapping: MapResult,
  records: Iterable<Record<string, unknown>>,
  store: AliasStore,
  entityIdResolver: EntityIdResolver,
  options: WriteAliasesOptions,
): Promise<AliasWriteResult> {
  if (store === null || store === undefined || typeof store.addAlias !== "function") {
    throw new Error(
      "writeAliasesFromMapping requires a goldenmatch IdentityStore " +
        "(an object exposing addAlias).",
    );
  }

  const {
    sourceName,
    minConfidence = DEFAULT_MIN_CONFIDENCE,
    dataset = null,
  } = options;
  const aliasKinds: ReadonlySet<string> =
    options.aliasKinds === undefined
      ? DEFAULT_ALIAS_KINDS
      : options.aliasKinds instanceof Set
        ? (options.aliasKinds as ReadonlySet<string>)
        : new Set(options.aliasKinds);
  const onError =
    options.onError ??
    (({ alias, kind, entityId, error }) => {
      // eslint-disable-next-line no-console
      console.warn(
        `Failed to write alias ${alias} (kind=${kind}) for entity ${entityId}: ${String(error)}`,
      );
    });

  // Pre-compute the usable (sourceCol, targetKind) tuples. Drops
  // low-confidence and non-alias-kind mappings upfront so we don't reread
  // per record.
  const usable: Array<[string, string]> = [];
  let skippedLowConfidence = 0;
  for (const m of mapping.mappings) {
    if (m.confidence < minConfidence) {
      skippedLowConfidence += 1;
      continue;
    }
    const kind = isAliasKind(m.target, aliasKinds);
    if (kind === null) continue;
    usable.push([m.source, kind]);
  }

  const summary: AliasWriteResult = {
    aliasesWritten: 0,
    recordsProcessed: 0,
    mappingsUsed: usable.length,
    skippedLowConfidence,
    skippedNoValue: 0,
    skippedNoEntity: 0,
    skippedNoKind: 0,
  };

  if (usable.length === 0) {
    return summary;
  }

  for (const record of records) {
    summary.recordsProcessed += 1;
    const entityId = await entityIdResolver(record);
    if (entityId === null || entityId === undefined) {
      summary.skippedNoEntity += 1;
      continue;
    }

    for (const [sourceCol, kind] of usable) {
      const value = record[sourceCol];
      if (value === null || value === undefined || value === "") {
        summary.skippedNoValue += 1;
        continue;
      }
      const aliasValue = `${sourceName}:${value}`;
      const aliasRow: IdentityAliasRow = {
        alias: aliasValue,
        entityId,
        kind,
        dataset,
        recordedAt: new Date(),
      };
      try {
        await store.addAlias(aliasRow);
        summary.aliasesWritten += 1;
      } catch (error) {
        // Don't blow up the whole batch on one bad row -- log and continue.
        // Identity is additive; partial writes are fine.
        onError({ alias: aliasValue, kind, entityId, error });
      }
    }
  }

  return summary;
}
