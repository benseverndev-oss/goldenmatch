/**
 * label-session.ts -- the `label` and `review` interactive loops, as pure-ish
 * functions over an injectable `Ask` so they can be tested without a TTY.
 *
 * Ports `goldenmatch/cli/label.py` and `goldenmatch/cli/review.py`.
 */
import type { Ask } from "./interactive.js";
import { askChoice, renderPair } from "./interactive.js";
import type { Row, ScoredPair } from "../core/types.js";

export type PairStrategy = "borderline" | "random" | "hardest";

/** Python's borderline anchor: the most ambiguous score. */
export const BORDERLINE_ANCHOR = 0.85;

export interface LabelRow {
  readonly id_a: number;
  readonly id_b: number;
  readonly label: 0 | 1;
  readonly score: number;
}

export interface LabelSessionResult {
  readonly labels: LabelRow[];
  readonly skipped: number;
  readonly quit: boolean;
}

/** Python's `round(x, 4)`. */
function round4(x: number): number {
  return Math.round(x * 10000) / 10000;
}

/**
 * Order candidate pairs. Mirrors Python exactly:
 *   borderline -> by |score - 0.85| ascending (most ambiguous first)
 *   hardest    -> by score ascending
 *   random     -> shuffled
 *
 * `rng` is injectable so the random strategy is deterministic under test.
 */
export function selectPairs(
  pairs: readonly ScoredPair[],
  strategy: PairStrategy,
  rng: () => number = Math.random,
): ScoredPair[] {
  const out = [...pairs];
  if (strategy === "borderline") {
    out.sort((a, b) => Math.abs(a.score - BORDERLINE_ANCHOR) - Math.abs(b.score - BORDERLINE_ANCHOR));
  } else if (strategy === "hardest") {
    out.sort((a, b) => a.score - b.score);
  } else {
    // Fisher-Yates with the injected rng.
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [out[i], out[j]] = [out[j]!, out[i]!];
    }
  }
  return out;
}

/**
 * Walk pairs one at a time collecting y/n/s/q decisions.
 *
 * Faithful to Python: `s` skips (counted, not labeled), `q` stops immediately,
 * already-labeled pairs are passed over in EITHER orientation, and the loop ends
 * once `n` labels are collected.
 */
export async function runLabelSession(opts: {
  pairs: readonly ScoredPair[];
  rowsById: ReadonlyMap<number, Row>;
  displayColumns: readonly string[];
  target: number;
  ask: Ask;
  existing?: ReadonlySet<string>;
  out?: (s: string) => void;
}): Promise<LabelSessionResult> {
  const { pairs, rowsById, displayColumns, target, ask } = opts;
  const existing = opts.existing ?? new Set<string>();
  const out = opts.out ?? (() => {});
  const labels: LabelRow[] = [];
  let skipped = 0;

  for (const p of pairs) {
    if (labels.length >= target) break;
    // Either orientation counts as already-labeled (Python checks both).
    if (existing.has(`${p.idA}:${p.idB}`) || existing.has(`${p.idB}:${p.idA}`)) continue;

    out(
      renderPair(
        rowsById.get(p.idA) ?? {},
        rowsById.get(p.idB) ?? {},
        displayColumns,
        `Pair ${labels.length + 1}/${target} (score: ${p.score.toFixed(3)})`,
      ),
    );

    const answer = await askChoice(ask, "[y/n/s/q] > ", ["y", "n", "s", "q"], "q", () =>
      out("Type y, n, s, or q"),
    );
    if (answer === "q") return { labels, skipped, quit: true };
    if (answer === "s") {
      skipped++;
      continue;
    }
    labels.push({
      id_a: p.idA,
      id_b: p.idB,
      label: answer === "y" ? 1 : 0,
      score: round4(p.score),
    });
  }
  return { labels, skipped, quit: false };
}

export interface ReviewDecision {
  readonly idA: number;
  readonly idB: number;
  readonly decision: "approve" | "reject";
}

export interface ReviewSessionResult {
  readonly decisions: ReviewDecision[];
  readonly skipped: number;
  readonly quit: boolean;
}

/**
 * The `review` loop. Same keys as `label`, but each y/n becomes an approve/reject
 * decision destined for Learning Memory rather than a ground-truth row.
 *
 * Returns the decisions instead of writing them, so the persistence step (and its
 * failure modes) stays outside the interactive loop and both are testable alone.
 */
export async function runReviewSession(opts: {
  items: readonly { idA: number; idB: number; score: number }[];
  rowsById: ReadonlyMap<number, Row>;
  displayColumns: readonly string[];
  ask: Ask;
  limit?: number;
  out?: (s: string) => void;
}): Promise<ReviewSessionResult> {
  const { items, rowsById, displayColumns, ask } = opts;
  const out = opts.out ?? (() => {});
  const limit = opts.limit ?? items.length;
  const decisions: ReviewDecision[] = [];
  let skipped = 0;

  for (const item of items.slice(0, limit)) {
    out(
      renderPair(
        rowsById.get(item.idA) ?? {},
        rowsById.get(item.idB) ?? {},
        displayColumns,
        `Record ${item.idA} vs ${item.idB} (score: ${item.score.toFixed(3)})`,
      ),
    );
    const answer = await askChoice(ask, "[y/n/s/q] > ", ["y", "n", "s", "q"], "q", () =>
      out("Type y, n, s, or q"),
    );
    if (answer === "q") return { decisions, skipped, quit: true };
    if (answer === "s") {
      skipped++;
      continue;
    }
    decisions.push({
      idA: item.idA,
      idB: item.idB,
      decision: answer === "y" ? "approve" : "reject",
    });
  }
  return { decisions, skipped, quit: false };
}
