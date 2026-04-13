/**
 * Confidence-gated review queue for GoldenCheck's DQ agent.
 * Port of goldencheck/agent/review_queue.py (in-memory backend only).
 * Edge-safe: no Node.js dependencies.
 */

import type { Finding } from "../types.js";
import { Severity, severityLabel } from "../types.js";

// ---------------------------------------------------------------------------
// ReviewItem
// ---------------------------------------------------------------------------

export interface ReviewItem {
  jobName: string;
  itemId: string;
  column: string;
  check: string;
  severity: string;
  confidence: number;
  message: string;
  explanation: string;
  sampleValues: readonly string[];
  status: "pending" | "approved" | "rejected";
  decidedBy: string | null;
  decidedAt: string | null;
}

// ---------------------------------------------------------------------------
// UUID helper (edge-safe, no crypto dependency required)
// ---------------------------------------------------------------------------

let _counter = 0;

function generateId(): string {
  // Use crypto.randomUUID if available, otherwise fall back to timestamp + counter
  if (typeof globalThis.crypto !== "undefined" && globalThis.crypto.randomUUID) {
    return globalThis.crypto.randomUUID().replace(/-/g, "");
  }
  _counter += 1;
  return `${Date.now().toString(36)}${_counter.toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

// ---------------------------------------------------------------------------
// Finding → ReviewItem conversion
// ---------------------------------------------------------------------------

function findingToReviewItem(finding: Finding, jobName: string): ReviewItem {
  return {
    jobName,
    itemId: generateId(),
    column: finding.column,
    check: finding.check,
    severity: severityLabel(finding.severity),
    confidence: finding.confidence,
    message: finding.message,
    explanation: finding.suggestion ?? "",
    sampleValues: [...finding.sampleValues],
    status: "pending",
    decidedBy: null,
    decidedAt: null,
  };
}

// ---------------------------------------------------------------------------
// ReviewQueue
// ---------------------------------------------------------------------------

export interface ReviewQueueStats {
  pending: number;
  approved: number;
  rejected: number;
}

export interface ClassifyResult {
  pinned: Finding[];
  review: ReviewItem[];
  dismissed: Finding[];
}

/**
 * Confidence-gated review queue with in-memory storage.
 *
 * Gating logic in classifyFindings:
 * - confidence >= 0.8 AND severity >= WARNING  -> pinned (auto-approved)
 * - 0.5 <= confidence < 0.8 AND severity >= WARNING -> review (added to queue)
 * - confidence < 0.5 OR severity == INFO -> dismissed
 */
export class ReviewQueue {
  private readonly _items = new Map<string, ReviewItem>();

  /** Add an item to the review queue. */
  add(item: ReviewItem): void {
    this._items.set(item.itemId, item);
  }

  /** Return all pending items for a job. */
  pending(jobName: string): ReviewItem[] {
    const result: ReviewItem[] = [];
    for (const item of this._items.values()) {
      if (item.jobName === jobName && item.status === "pending") {
        result.push(item);
      }
    }
    return result;
  }

  /** Mark an item as approved. */
  approve(itemId: string, decidedBy: string, _reason?: string): void {
    const item = this._items.get(itemId);
    if (!item) {
      throw new Error(`ReviewItem '${itemId}' not found`);
    }
    item.status = "approved";
    item.decidedBy = decidedBy;
    item.decidedAt = new Date().toISOString();
  }

  /** Mark an item as rejected. */
  reject(itemId: string, decidedBy: string, _reason?: string): void {
    const item = this._items.get(itemId);
    if (!item) {
      throw new Error(`ReviewItem '${itemId}' not found`);
    }
    item.status = "rejected";
    item.decidedBy = decidedBy;
    item.decidedAt = new Date().toISOString();
  }

  /** Return counts by status for a job. */
  stats(jobName: string): ReviewQueueStats {
    const counts: ReviewQueueStats = { pending: 0, approved: 0, rejected: 0 };
    for (const item of this._items.values()) {
      if (item.jobName === jobName) {
        if (item.status === "pending") counts.pending += 1;
        else if (item.status === "approved") counts.approved += 1;
        else if (item.status === "rejected") counts.rejected += 1;
      }
    }
    return counts;
  }

  /**
   * Gate findings by confidence and severity.
   *
   * - confidence >= 0.8 AND severity >= WARNING  -> pinned
   * - 0.5 <= confidence < 0.8 AND severity >= WARNING -> review (queued)
   * - confidence < 0.5 OR severity == INFO -> dismissed
   */
  classifyFindings(findings: readonly Finding[], jobName: string): ClassifyResult {
    const pinned: Finding[] = [];
    const review: ReviewItem[] = [];
    const dismissed: Finding[] = [];

    for (const finding of findings) {
      const highSeverity = finding.severity >= Severity.WARNING;

      if (finding.confidence >= 0.8 && highSeverity) {
        pinned.push(finding);
      } else if (finding.confidence >= 0.5 && highSeverity) {
        const item = findingToReviewItem(finding, jobName);
        this._items.set(item.itemId, item);
        review.push(item);
      } else {
        dismissed.push(finding);
      }
    }

    return { pinned, review, dismissed };
  }
}
