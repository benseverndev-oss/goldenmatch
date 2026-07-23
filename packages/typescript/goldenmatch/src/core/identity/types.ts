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
  | "derived_from"
  | "mediation_verdict";

export type EventKind =
  | "created"
  | "absorbed_record"
  | "merged_with"
  | "split_from"
  | "retired"
  | "manual_merge"
  | "manual_split"
  // v3 (#1112): auto-consolidation of persistently-overlapping entities across
  // runs (distinct from `manual_merge` -- no human in the loop).
  | "consolidated"
  | "claimed"
  | "conflict_mediated"
  // Claim lifecycle (#1256): explicit, auditable transitions of a claim's
  // authority (promote raises a tier, amend supersedes content, revoke retracts).
  | "promote"
  | "amend"
  | "revoke";

/**
 * Categorical authority of a claim (#1256), ORTHOGONAL to numeric `trust`.
 * `trust` is the confidence WITHIN a tier; `claimType` is the tier itself, so a
 * reviewer can tell "an agent inferred this at 0.8" from "a tool verified this
 * at 0.8". Mirrors Python `model.py::ClaimType`.
 */
export type ClaimType =
  | "observation" // agent saw this in a session
  | "inference" // agent concluded this; needs revalidation
  | "verified" // backed by tool output / source / test / user
  | "directive"; // human-authorized rule or constraint

/**
 * What backs a claim (#1256) -- the typed provenance of the evidence. Mirrors
 * Python `model.py::EvidenceRef`.
 */
export type EvidenceRef =
  | "tool-call"
  | "source"
  | "user-confirmation"
  | "test-run";

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
  // Provenance spine (#1075/#1078): WHO created this write and their trust.
  // `actor` is a free-form principal id ("pipeline", "agent:<name>",
  // "steward:<user>"); `trust` in [0, 1]. Optional/nullable -- pipeline-driven
  // and pre-provenance edges carry neither, matching Python byte-for-byte.
  actor?: string | null;
  trust?: number | null;
  recordedAt: Date;
}

export interface IdentityEvent {
  eventId: number | null;
  entityId: string;
  kind: EventKind;
  payload: Record<string, unknown> | null;
  runName: string | null;
  dataset: string | null;
  // Provenance spine (#1075/#1078): WHO made this change and their trust. The
  // "why" rides in `payload.reason`. See `EvidenceEdge` for the contract.
  actor?: string | null;
  trust?: number | null;
  // Claim-authority tier (#1256): categorical authority (`claimType`) + typed
  // backing evidence (`evidenceRef`) + the lifecycle chain (`previousClaimId`
  // -> the event this claim supersedes). All optional/nullable and additive.
  claimType?: ClaimType | null;
  evidenceRef?: EvidenceRef | null;
  previousClaimId?: number | null;
  // Tamper-evidence (#1078): per-event content hash, computed at insert by the
  // audit layer (PR-B). Nullable -- pre-hash-chain rows read back as null.
  entryHash?: string | null;
  recordedAt: Date;
}

/**
 * A periodic tamper-evidence anchor over the append-only event log (#1078).
 * Mirrors Python `model.py::AuditSeal`. Each seal records the chained root hash
 * of every event (in `eventId` order) up to `lastEventId` for a given
 * `dataset` scope (`null` = global chain); seals chain to their predecessor via
 * `prevSealId` / `prevRoot`. Created on demand by the audit layer (PR-B).
 */
export interface AuditSeal {
  sealId: number | null;
  rootHash: string;
  eventCount: number;
  lastEventId: number | null;
  dataset: string | null;
  prevSealId: number | null;
  prevRoot: string | null;
  actor: string | null;
  createdAt: Date;
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
  /**
   * All evidence edges of a given `kind`, most-recent first. Generic
   * counterpart to `findConflicts` (which is `edgesByKind("conflicts_with")`).
   * Mirrors Python `store.edges_by_kind`; used by the mediation workflow to
   * list steward verdict edges. Ties on `recordedAt` break on descending
   * insertion order so latest-wins lookups are deterministic.
   */
  edgesByKind(kind: EdgeKind, dataset?: string): Promise<EvidenceEdge[]>;

  emitEvent(event: IdentityEvent): Promise<number | null>;
  history(entityId: string, limit?: number): Promise<IdentityEvent[]>;
  hasRunEvent(entityId: string, runName: string, kind: EventKind): Promise<boolean>;

  /**
   * The full append-only event log in commit order (`eventId` ASC), for
   * compliance review/export (#1078). `dataset` scopes to a dataset (`null` =
   * global). Mirrors Python `store.export_audit_log` (the `dataset` filter;
   * Python's `actor`/`since` filters are a PR-B follow-up).
   */
  exportAuditLog(dataset?: string): Promise<IdentityEvent[]>;

  /** Persist a tamper-evidence seal and return its id. Mirrors Python `store.add_seal`. */
  addSeal(seal: AuditSeal): Promise<number | null>;
  /**
   * The most recent seal for the given `dataset` scope (`null`/omitted =
   * global chain), or `null` if empty. Mirrors Python `store.latest_seal`.
   */
  latestSeal(dataset?: string): Promise<AuditSeal | null>;
  /** Every seal for the given `dataset` scope in creation order (`sealId` ASC). */
  listSeals(dataset?: string): Promise<AuditSeal[]>;

  addAlias(alias: IdentityAlias): Promise<void>;
  resolveAlias(alias: string, kind?: string): Promise<string | null>;

  close(): Promise<void>;
}

/** Canonicalize record id pair to (min, max) lex order. */
export function canonRecordPair(a: string, b: string): readonly [string, string] {
  return a <= b ? [a, b] : [b, a];
}
