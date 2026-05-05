/**
 * review-queue.ts — Human-in-the-loop pair gating.
 * Edge-safe: no Node.js imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/review_queue.py. Default gates: >=0.95 auto-approve,
 * <0.75 auto-reject, everything in between needs review.
 */

import type { Row, ScoredPair } from "./types.js";
import type {
  Decision,
  MemoryStore,
} from "./memory/types.js";
import { trustForSource } from "./memory/types.js";
import {
  computeFieldHash,
  computeRecordHash,
} from "./memory/hash.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReviewStatus = "pending" | "approved" | "rejected";

export interface ReviewItem {
  readonly pairId: string;
  readonly idA: number;
  readonly idB: number;
  readonly score: number;
  readonly status: ReviewStatus;
  readonly createdAt: number;
  /**
   * Optional human-readable one-liner explaining why this pair was flagged.
   * Populated by `whyForCorrection` (deterministic) or `llmExplainPair`
   * (when an OPENAI_API_KEY / ANTHROPIC_API_KEY is set). Surfaces in the
   * review TUI / REST `/reviews` payload.
   */
  readonly why?: string;
}

export interface GatedResult {
  readonly autoApproved: readonly ScoredPair[];
  readonly needsReview: readonly ReviewItem[];
  readonly rejected: readonly ScoredPair[];
}

export interface GateOptions {
  readonly approveAbove?: number;
  readonly rejectBelow?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function canonicalIds(a: number, b: number): [number, number] {
  return a < b ? [a, b] : [b, a];
}

function pairIdFor(a: number, b: number): string {
  const [lo, hi] = canonicalIds(a, b);
  return `${lo}:${hi}`;
}

function now(): number {
  // Date.now is edge-safe (no node imports).
  return Date.now();
}

// ---------------------------------------------------------------------------
// gatePairs
// ---------------------------------------------------------------------------

/**
 * Split pairs into auto-approved, needs-review, and rejected buckets.
 *
 * Defaults: approveAbove=0.95, rejectBelow=0.75.
 */
export function gatePairs(
  pairs: readonly ScoredPair[],
  options?: GateOptions,
): GatedResult {
  const approveAbove = options?.approveAbove ?? 0.95;
  const rejectBelow = options?.rejectBelow ?? 0.75;

  const autoApproved: ScoredPair[] = [];
  const needsReview: ReviewItem[] = [];
  const rejected: ScoredPair[] = [];
  const t = now();

  for (const p of pairs) {
    if (p.score >= approveAbove) {
      autoApproved.push(p);
    } else if (p.score < rejectBelow) {
      rejected.push(p);
    } else {
      const [lo, hi] = canonicalIds(p.idA, p.idB);
      needsReview.push({
        pairId: `${lo}:${hi}`,
        idA: lo,
        idB: hi,
        score: p.score,
        status: "pending",
        createdAt: t,
      });
    }
  }

  return { autoApproved, needsReview, rejected };
}

// ---------------------------------------------------------------------------
// ReviewQueue
// ---------------------------------------------------------------------------

/**
 * In-memory review queue for human adjudication of borderline pairs.
 */
export class ReviewQueue {
  private readonly items = new Map<string, ReviewItem>();

  /** Add a pair as a pending review item (idempotent by canonical pair id). */
  add(pair: ScoredPair): void {
    const [lo, hi] = canonicalIds(pair.idA, pair.idB);
    const pairId = `${lo}:${hi}`;
    if (this.items.has(pairId)) return;
    this.items.set(pairId, {
      pairId,
      idA: lo,
      idB: hi,
      score: pair.score,
      status: "pending",
      createdAt: now(),
    });
  }

  /** Get an item by canonical pair id ("minId:maxId"). */
  get(pairId: string): ReviewItem | undefined {
    return this.items.get(pairId);
  }

  /** Mark a pair approved. No-op if unknown. */
  approve(pairId: string, opts?: ReviewMemoryOpts): Promise<void> | void {
    const item = this.items.get(pairId);
    if (item === undefined) return;
    this.items.set(pairId, { ...item, status: "approved" });
    if (opts?.memoryStore) {
      return _writeReviewCorrection(item, "approve", opts);
    }
  }

  /** Mark a pair rejected. No-op if unknown. */
  reject(pairId: string, opts?: ReviewMemoryOpts): Promise<void> | void {
    const item = this.items.get(pairId);
    if (item === undefined) return;
    this.items.set(pairId, { ...item, status: "rejected" });
    if (opts?.memoryStore) {
      return _writeReviewCorrection(item, "reject", opts);
    }
  }

  /** All pending items. */
  pending(): ReviewItem[] {
    const out: ReviewItem[] = [];
    for (const item of this.items.values()) {
      if (item.status === "pending") out.push(item);
    }
    return out;
  }

  /** All approved items. */
  approved(): ReviewItem[] {
    const out: ReviewItem[] = [];
    for (const item of this.items.values()) {
      if (item.status === "approved") out.push(item);
    }
    return out;
  }

  /** All rejected items. */
  rejected(): ReviewItem[] {
    const out: ReviewItem[] = [];
    for (const item of this.items.values()) {
      if (item.status === "rejected") out.push(item);
    }
    return out;
  }

  /** Current queue size. */
  size(): number {
    return this.items.size;
  }

  /** Canonical pair id helper ("minId:maxId"). */
  static pairIdFor(a: number, b: number): string {
    return pairIdFor(a, b);
  }
}

// ---------------------------------------------------------------------------
// Memory collection (Phase 2.4.1)
// ---------------------------------------------------------------------------

/**
 * Options accepted by `ReviewQueue.approve` / `reject` to collect a
 * `Correction` from a steward decision. When `df` + `matchkeyFields` are
 * supplied, dual hashes are computed; otherwise empty hashes (still applied
 * via short-circuit on row-id match).
 */
export interface ReviewMemoryOpts {
  readonly memoryStore: MemoryStore;
  readonly df?: ReadonlyArray<Row>;
  readonly matchkeyFields?: ReadonlyArray<string>;
  readonly dataset?: string | null;
  readonly matchkeyName?: string | null;
}

async function _writeReviewCorrection(
  item: ReviewItem,
  decision: Decision,
  opts: ReviewMemoryOpts,
): Promise<void> {
  let fieldHash = "";
  let recordHash = "";
  if (opts.df && opts.df.length > 0) {
    const cols = Object.keys(opts.df[0]!);
    const rowById = new Map<number, Row>();
    for (const r of opts.df) {
      const rid = r["__row_id__"];
      if (typeof rid === "number") rowById.set(rid, r);
    }
    const rowA = rowById.get(item.idA);
    const rowB = rowById.get(item.idB);
    if (rowA && rowB) {
      const fields = (opts.matchkeyFields ?? []).filter((f) => cols.includes(f));
      const valsA = fields.map((f) => rowA[f]);
      const valsB = fields.map((f) => rowB[f]);
      fieldHash = await computeFieldHash(valsA, valsB);
      const rhA = await computeRecordHash(rowA, cols);
      const rhB = await computeRecordHash(rowB, cols);
      recordHash = `${rhA}:${rhB}`;
    }
  }
  await opts.memoryStore.addCorrection({
    id: crypto.randomUUID(),
    idA: item.idA,
    idB: item.idB,
    decision,
    source: "steward",
    trust: trustForSource("steward"),
    fieldHash,
    recordHash,
    originalScore: item.score,
    matchkeyName: opts.matchkeyName ?? null,
    reason: null,
    dataset: opts.dataset ?? null,
    createdAt: new Date(),
  });
}
