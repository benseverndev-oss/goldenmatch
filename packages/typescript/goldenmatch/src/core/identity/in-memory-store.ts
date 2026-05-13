/**
 * In-memory IdentityStore -- edge-safe (no node imports). Parity with the
 * Python sibling's semantics for testing and edge runtime use; persistent
 * SQLite store lives in `src/node/identity/`.
 */

import {
  canonRecordPair,
  type EventKind,
  type EvidenceEdge,
  type IdentityAlias,
  type IdentityEvent,
  type IdentityNode,
  type IdentityStatus,
  type IdentityStore,
  type SourceRecord,
} from "./types.js";

export class InMemoryIdentityStore implements IdentityStore {
  private readonly identities = new Map<string, IdentityNode>();
  private readonly records = new Map<string, SourceRecord>();
  private readonly edges: EvidenceEdge[] = [];
  private readonly events: IdentityEvent[] = [];
  private readonly aliases = new Map<string, IdentityAlias>();
  private nextEdgeId = 1;
  private nextEventId = 1;

  async upsertIdentity(node: IdentityNode): Promise<void> {
    const existing = this.identities.get(node.entityId);
    if (existing) {
      this.identities.set(node.entityId, {
        ...node,
        createdAt: existing.createdAt,
        updatedAt: node.updatedAt,
      });
    } else {
      this.identities.set(node.entityId, { ...node });
    }
  }

  async getIdentity(entityId: string): Promise<IdentityNode | null> {
    const n = this.identities.get(entityId);
    return n ? { ...n } : null;
  }

  async listIdentities(opts: {
    dataset?: string;
    status?: IdentityStatus;
    limit?: number;
    offset?: number;
  } = {}): Promise<IdentityNode[]> {
    const all = Array.from(this.identities.values())
      .filter((n) => opts.dataset === undefined || n.dataset === opts.dataset)
      .filter((n) => opts.status === undefined || n.status === opts.status)
      .sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime());
    const offset = opts.offset ?? 0;
    const limit = opts.limit ?? 100;
    return all.slice(offset, offset + limit).map((n) => ({ ...n }));
  }

  async countIdentities(dataset?: string): Promise<number> {
    if (dataset === undefined) return this.identities.size;
    let n = 0;
    for (const node of this.identities.values()) {
      if (node.dataset === dataset) n++;
    }
    return n;
  }

  async retireIdentity(entityId: string, mergedInto?: string): Promise<void> {
    const node = this.identities.get(entityId);
    if (!node) return;
    const next: IdentityNode = {
      ...node,
      status: mergedInto ? "merged_into" : "retired",
      mergedInto: mergedInto ?? null,
      updatedAt: new Date(),
    };
    this.identities.set(entityId, next);
  }

  async upsertRecord(rec: SourceRecord): Promise<void> {
    const existing = this.records.get(rec.recordId);
    if (existing) {
      this.records.set(rec.recordId, {
        ...rec,
        firstSeenAt: existing.firstSeenAt,
        lastSeenAt: rec.lastSeenAt,
      });
    } else {
      this.records.set(rec.recordId, { ...rec });
    }
  }

  async getRecord(recordId: string): Promise<SourceRecord | null> {
    const r = this.records.get(recordId);
    return r ? { ...r } : null;
  }

  async getRecordsForEntity(entityId: string): Promise<SourceRecord[]> {
    return Array.from(this.records.values())
      .filter((r) => r.entityId === entityId)
      .sort((a, b) => a.firstSeenAt.getTime() - b.firstSeenAt.getTime())
      .map((r) => ({ ...r }));
  }

  async findEntityByRecord(recordId: string): Promise<string | null> {
    return this.records.get(recordId)?.entityId ?? null;
  }

  async lookupEntityIds(recordIds: readonly string[]): Promise<Map<string, string>> {
    const out = new Map<string, string>();
    for (const rid of recordIds) {
      const eid = this.records.get(rid)?.entityId;
      if (eid) out.set(rid, eid);
    }
    return out;
  }

  async addEdge(edge: EvidenceEdge): Promise<number | null> {
    const [a, b] = canonRecordPair(edge.recordAId, edge.recordBId);
    const runKey = edge.runName ?? "";
    // Dedup on (entity_id, a, b, run_name) like the UNIQUE constraint.
    for (const e of this.edges) {
      if (
        e.entityId === edge.entityId &&
        e.recordAId === a &&
        e.recordBId === b &&
        (e.runName ?? "") === runKey
      ) {
        return e.edgeId;
      }
    }
    const stored: EvidenceEdge = {
      ...edge,
      recordAId: a,
      recordBId: b,
      edgeId: this.nextEdgeId++,
    };
    this.edges.push(stored);
    return stored.edgeId;
  }

  async edgesForEntity(entityId: string): Promise<EvidenceEdge[]> {
    return this.edges
      .filter((e) => e.entityId === entityId)
      .sort((a, b) => a.recordedAt.getTime() - b.recordedAt.getTime())
      .map((e) => ({ ...e }));
  }

  async findConflicts(dataset?: string): Promise<EvidenceEdge[]> {
    return this.edges
      .filter((e) => e.kind === "conflicts_with")
      .filter((e) => dataset === undefined || e.dataset === dataset)
      .sort((a, b) => b.recordedAt.getTime() - a.recordedAt.getTime())
      .map((e) => ({ ...e }));
  }

  async emitEvent(event: IdentityEvent): Promise<number | null> {
    const stored: IdentityEvent = { ...event, eventId: this.nextEventId++ };
    this.events.push(stored);
    return stored.eventId;
  }

  async history(entityId: string, limit?: number): Promise<IdentityEvent[]> {
    const filtered = this.events
      .filter((e) => e.entityId === entityId)
      .sort((a, b) => (a.eventId ?? 0) - (b.eventId ?? 0));
    return (limit ? filtered.slice(0, limit) : filtered).map((e) => ({ ...e }));
  }

  async hasRunEvent(entityId: string, runName: string, kind: EventKind): Promise<boolean> {
    return this.events.some(
      (e) => e.entityId === entityId && e.runName === runName && e.kind === kind,
    );
  }

  async addAlias(alias: IdentityAlias): Promise<void> {
    this.aliases.set(`${alias.alias}|${alias.kind}|${alias.dataset ?? ""}`, { ...alias });
  }

  async resolveAlias(alias: string, kind = "external_id"): Promise<string | null> {
    for (const [key, val] of this.aliases.entries()) {
      const parts = key.split("|");
      if (parts[0] === alias && parts[1] === kind) return val.entityId;
    }
    return null;
  }

  async close(): Promise<void> {
    // no-op for in-memory
  }
}
