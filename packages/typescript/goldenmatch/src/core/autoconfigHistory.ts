/**
 * autoconfigHistory.ts — RunHistory + audit-trail records for the controller.
 *
 * Port of Python ``goldenmatch/core/autoconfig_history.py`` (v1.7/v1.8).
 * Edge-safe: no `node:` imports.
 */

import type { GoldenMatchConfig } from "./types.js";
import type { ExecutionPlan } from "./executionPlan.js";
import {
  type ComplexityProfile,
  type StopReason,
  HealthVerdict,
  complexityHealth,
  normalizedSignalVector,
  makeDataProfile,
  makeComplexityProfile,
} from "./complexityProfile.js";

// ---------------------------------------------------------------------------
// Audit-trail dataclass equivalents
// ---------------------------------------------------------------------------

export interface PolicyDecision {
  readonly ruleName: string;
  readonly rationale: string;
  readonly configDiff: Readonly<Record<string, unknown>>;
}

export interface ErrorRecord {
  readonly exceptionType: string;
  readonly tracebackSummary: string;
}

export interface HistoryEntry {
  readonly iteration: number;
  readonly config: GoldenMatchConfig;
  readonly profile: ComplexityProfile;
  /** Mutable so the policy can attach a decision after-the-fact (mirrors Python). */
  decision: PolicyDecision | null;
  readonly error: ErrorRecord | null;
  readonly wallClockMs: number;
}

/** Sentinel profile that rolls up to RED via dataHealth (nRows=0). */
export const RED_PROFILE: ComplexityProfile = makeComplexityProfile({
  data: makeDataProfile({ nRows: 0 }),
});

// ---------------------------------------------------------------------------
// RunHistory
// ---------------------------------------------------------------------------

const HEALTH_RANK: Record<HealthVerdict, number> = {
  [HealthVerdict.GREEN]: 0,
  [HealthVerdict.YELLOW]: 1,
  [HealthVerdict.RED]: 2,
};

export class RunHistory {
  readonly entries: HistoryEntry[] = [];
  fullVsSampleDrift: number | null = null;
  elapsedMs = 0;
  stopReason: StopReason | null = null;
  /** Controller-v3 planner output (set after pickCommitted). Null until the
   *  planner runs. Mirrors Python ``RunHistory.execution_plan``. */
  executionPlan: ExecutionPlan | null = null;

  get iteration(): number {
    return this.entries.length;
  }

  get decisions(): readonly PolicyDecision[] {
    const out: PolicyDecision[] = [];
    for (const e of this.entries) if (e.decision !== null) out.push(e.decision);
    return out;
  }

  get errors(): readonly ErrorRecord[] {
    const out: ErrorRecord[] = [];
    for (const e of this.entries) if (e.error !== null) out.push(e.error);
    return out;
  }

  append(entry: HistoryEntry): void {
    this.entries.push(entry);
  }

  /**
   * Same (configHash, decisionHash) pair appears >=2x in last 4 iterations.
   * Mirrors Python ``is_oscillating``.
   */
  isOscillating(): boolean {
    const window = this.entries.slice(-4);
    if (window.length < 4) return false;
    const sigCounts = new Map<string, number>();
    for (const e of window) {
      const cfg = JSON.stringify(e.config);
      const dec = e.decision ? e.decision.ruleName : "";
      const sig = `${cfg}::${dec}`;
      sigCounts.set(sig, (sigCounts.get(sig) ?? 0) + 1);
    }
    for (const c of sigCounts.values()) if (c >= 2) return true;
    return false;
  }

  /** L1 distance between last two profiles' normalized signal vectors. */
  profileDistanceToPrev(): number {
    if (this.entries.length < 2) return Number.POSITIVE_INFINITY;
    const a = normalizedSignalVector(this.entries[this.entries.length - 1]!.profile);
    const b = normalizedSignalVector(this.entries[this.entries.length - 2]!.profile);
    let s = 0;
    for (let i = 0; i < a.length; i++) s += Math.abs((a[i] ?? 0) - (b[i] ?? 0));
    return s;
  }

  /**
   * Best-effort commit selection. Lex key: ``(healthRank, -massSeparation, iteration)``.
   * Returns ``null`` only when every entry errored or lacks a profile.
   * When ``precisionCollapseFloor`` is set (typical 0.9), RED entries with
   * ``massAboveThreshold > floor`` get demoted to rank=3 — guards the
   * "everything matches" pathology.
   */
  pickCommitted(precisionCollapseFloor: number | null = null): HistoryEntry | null {
    if (
      precisionCollapseFloor !== null &&
      !(precisionCollapseFloor >= 0 && precisionCollapseFloor <= 1)
    ) {
      throw new RangeError(
        `precisionCollapseFloor must be in [0, 1]; got ${precisionCollapseFloor}`,
      );
    }
    const survivors = this.entries.filter((e) => e.error === null);
    if (survivors.length === 0) return null;

    let best: HistoryEntry | null = null;
    let bestKey: [number, number, number] | null = null;
    for (const e of survivors) {
      const verdict = complexityHealth(e.profile);
      let rank = HEALTH_RANK[verdict];
      const sp = e.profile.scoring;
      const sep = sp.massAboveThreshold - sp.massInBorderline;
      if (
        precisionCollapseFloor !== null &&
        verdict === HealthVerdict.RED &&
        sp.massAboveThreshold > precisionCollapseFloor
      ) {
        rank = 3;
      }
      const key: [number, number, number] = [rank, -sep, e.iteration];
      if (bestKey === null || cmpKey(key, bestKey) < 0) {
        best = e;
        bestKey = key;
      }
    }
    return best;
  }
}

function cmpKey(
  a: readonly [number, number, number],
  b: readonly [number, number, number],
): number {
  if (a[0] !== b[0]) return a[0] - b[0];
  if (a[1] !== b[1]) return a[1] - b[1];
  return a[2] - b[2];
}
