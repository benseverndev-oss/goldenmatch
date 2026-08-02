/**
 * serving.ts — certify the join keys a Customer 360 serving layer rolls up on.
 *
 * Edge-safe port of Python `semantic/serving.py::certify_serving_joins` +
 * `ServingJoinCertificate`.
 *
 * A Customer 360 view joins the golden record to its source records (and events /
 * relationships) on the durable `entityId`, and a dashboard typically joins a
 * fact table to those source records on `recordId` (`{source}:{sourcePk}`) before
 * rolling up to the entity. If a `recordId` is duplicated, that roll-up silently
 * double-counts. This walks the store's entities, assembles the `recordId` join
 * key, and runs `certifyKeyIntegrity` over it — turning the serving layer's
 * implicit join-key trust into an advisory `KeyIntegrityCertificate`.
 */
import type { IdentityStore, IdentityNode } from "../identity/types.js";
import { KeyIntegrityCertificate, certifyKeyIntegrity } from "./keyIntegrity.js";

const PAGE = 500;

/** Certifies the join keys a Customer 360 serving layer rolls metrics up on.
 *
 * `recordCertificate` is a `KeyIntegrityCertificate` over the source-record join
 * key (`recordId` = `{source}:{sourcePk}`): unique means a fact joined to source
 * records and rolled up to the entity can't double-count. `nEntities` / `nRecords`
 * are the population it was computed over; `truncated` is true when a `maxEntities`
 * cap stopped the scan short (so the cert covers a prefix). */
export class ServingJoinCertificate {
  constructor(
    readonly recordCertificate: KeyIntegrityCertificate,
    readonly nEntities: number,
    readonly nRecords: number,
    readonly truncated: boolean = false,
  ) {}

  get isTrustworthy(): boolean {
    return this.recordCertificate.isTrustworthy();
  }
}

/**
 * Certify that a Customer 360 serving layer's join keys don't double-count.
 * Mirrors Python `certify_serving_joins`.
 *
 * @param store  an open `IdentityStore`.
 * @param opts.dataset  restrict to one identity-graph dataset (default: all).
 * @param opts.status   entity status to include (default `"active"`; `null` = all).
 * @param opts.pageSize pagination size for the entity scan.
 * @param opts.maxEntities  cap the scan at this many entities (the cert then
 *   covers a prefix and sets `truncated=true`); omit to scan every entity.
 */
export async function certifyServingJoins(
  store: IdentityStore,
  opts: { dataset?: string | null; status?: string | null; pageSize?: number; maxEntities?: number | null } = {},
): Promise<ServingJoinCertificate> {
  const dataset = opts.dataset ?? null;
  const status = opts.status === undefined ? "active" : opts.status;
  const pageSize = opts.pageSize ?? PAGE;
  const maxEntities = opts.maxEntities ?? null;

  const recordIds: string[] = [];
  const entityIds: string[] = [];
  let offset = 0;
  let truncated = false;

  for (;;) {
    let limit = pageSize;
    if (maxEntities !== null) {
      const remaining = maxEntities - entityIds.length;
      if (remaining <= 0) {
        truncated = true;
        break;
      }
      limit = Math.min(pageSize, remaining);
    }
    const nodes = await store.listIdentities({
      ...(dataset !== null ? { dataset } : {}),
      ...(status !== null ? { status: status as IdentityNode["status"] } : {}),
      limit,
      offset,
    });
    if (nodes.length === 0) break;
    for (const node of nodes) {
      entityIds.push(node.entityId);
      for (const rec of await store.getRecordsForEntity(node.entityId)) {
        recordIds.push(rec.recordId);
      }
    }
    offset += nodes.length;
    if (nodes.length < limit) break;
  }

  // certifyKeyIntegrity needs at least one row; an empty store is trivially
  // trustworthy (no records means nothing can double-count).
  const cert = certifyKeyIntegrity({ record_id: recordIds.length ? recordIds : [null] }, { key: "record_id" });
  return new ServingJoinCertificate(cert, entityIds.length, recordIds.length, truncated);
}
