/**
 * Identity Graph types -- TS parity with
 * `packages/python/goldenmatch/goldenmatch/identity/model.py` +
 * `packages/python/goldenmatch/goldenmatch/identity/store.py` (interface).
 *
 * Edge-safe: no `node:*` imports here. Persistent backend lives in
 * `src/node/identity/`.
 */

export type IdentityStatus =
  | "active"
  | "merged_into"
  | "split"
  | "retired";

export type EdgeKind =
  | "same_as"
  | "possible_same_as"
  | "conflicts_with"
  | "derived_from";

export type EventKind =
  | "created"
  | "absorbed_record"
  | "merged_with"
  | "split_from"
  | "retired"
  | "manual_merge"
  | "manual_split";

export interface IdentityNode {
  entityId: string;
  status: IdentityStatus;
  mergedInto: string | null;
  goldenRecord: Record<string, unknown> | null;
  confidence: number | null;
  dataset: string | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface SourceRecord {
  recordId: string;
  source: string;
  sourcePk: string;
  recordHash: string;
  entityId: string | null;
  payload: Record<string, unknown> | null;
  dataset: string | null;
  firstSeenAt: Date;
  lastSeenAt: Date;
}

export interface EvidenceEdge {
  edgeId: number | null;
  entityId: string;
  recordAId: string;
  recordBId: string;
  kind: EdgeKind;
  score: number | null;
  matchkeyName: string | null;
  fieldScores: Record<string, unknown> | null;
  negativeEvidence: Record<string, unknown> | null;
  controllerSnapshot: Record<string, unknown> | null;
  runName: string | null;
  dataset: string | null;
  recordedAt: Date;
}

export interface IdentityEvent {
  eventId: number | null;
  entityId: string;
  kind: EventKind;
  payload: Record<string, unknown> | null;
  runName: string | null;
  dataset: string | null;
  recordedAt: Date;
}

export interface IdentityAlias {
  alias: string;
  entityId: string;
  kind: string;
  dataset: string | null;
  recordedAt: Date;
}

export interface IdentityConfig {
  enabled: boolean;
  backend: "sqlite" | "memory";
  path: string;
  dataset: string | null;
  sourcePkColumn: string | null;
  emitSingletons: boolean;
}

/**
 * Async store interface. The in-memory implementation and SQLite-backed
 * implementation both satisfy this so callers don't branch on backend.
 */
export interface IdentityStore {
  upsertIdentity(node: IdentityNode): Promise<void>;
  getIdentity(entityId: string): Promise<IdentityNode | null>;
  listIdentities(opts?: {
    dataset?: string;
    status?: IdentityStatus;
    limit?: number;
    offset?: number;
  }): Promise<IdentityNode[]>;
  countIdentities(dataset?: string): Promise<number>;
  retireIdentity(entityId: string, mergedInto?: string): Promise<void>;

  upsertRecord(rec: SourceRecord): Promise<void>;
  getRecord(recordId: string): Promise<SourceRecord | null>;
  getRecordsForEntity(entityId: string): Promise<SourceRecord[]>;
  findEntityByRecord(recordId: string): Promise<string | null>;
  lookupEntityIds(recordIds: readonly string[]): Promise<Map<string, string>>;

  addEdge(edge: EvidenceEdge): Promise<number | null>;
  edgesForEntity(entityId: string): Promise<EvidenceEdge[]>;
  findConflicts(dataset?: string): Promise<EvidenceEdge[]>;

  emitEvent(event: IdentityEvent): Promise<number | null>;
  history(entityId: string, limit?: number): Promise<IdentityEvent[]>;
  hasRunEvent(entityId: string, runName: string, kind: EventKind): Promise<boolean>;

  addAlias(alias: IdentityAlias): Promise<void>;
  resolveAlias(alias: string, kind?: string): Promise<string | null>;

  close(): Promise<void>;
}

/** Canonicalize record id pair to (min, max) lex order. */
export function canonRecordPair(a: string, b: string): readonly [string, string] {
  return a <= b ? [a, b] : [b, a];
}
