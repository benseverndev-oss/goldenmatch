// Compare a current report against a baseline, producing deltas.
// Mirrors infermap_bench/compare.py.

interface MetricSet {
  f1: number;
  top1: number;
  mrr: number;
  ece: number;
  n: number;
}

interface Report {
  version: 1;
  language: string;
  infermap_version: string;
  runner_version: string;
  ran_at: string;
  duration_seconds: number;
  scorecard: {
    overall: MetricSet;
    by_difficulty: Record<string, MetricSet>;
    by_category: Record<string, MetricSet>;
    by_tag: Record<string, MetricSet>;
  };
  per_case: Array<{
    id: string;
    f1: number;
    top1: number;
    mrr: number;
    expected_n: number;
    predicted_n: number;
    true_positives: number;
    false_positives: number;
    false_negatives: number;
    failed: boolean;
    failure_reason: string | null;
  }>;
  failed_cases: string[];
}

const METRIC_KEYS = ["f1", "top1", "mrr", "ece"] as const;
type MetricKey = (typeof METRIC_KEYS)[number];

const ZERO_METRIC_SET: MetricSet = { f1: 0, top1: 0, mrr: 0, ece: 0, n: 0 };

export interface Mover {
  caseId: string;
  baselineF1: number;
  currentF1: number;
  deltaF1: number;
}

export class Delta {
  constructor(
    public readonly overall: Record<MetricKey, number> = { f1: 0, top1: 0, mrr: 0, ece: 0 },
    public readonly byDifficulty: Record<string, Record<MetricKey, number>> = {},
    public readonly byCategory: Record<string, Record<MetricKey, number>> = {},
    public readonly byTag: Record<string, Record<MetricKey, number>> = {},
    public readonly perCaseDeltas: ReadonlyArray<readonly [string, number, number]> = [],
  ) {}

  /**
   * True iff overall F1 dropped by strictly more than `threshold`.
   * Uses a `1e-9` IEEE-754 epsilon guard so a drop exactly equal to
   * `threshold` is NOT classified as a regression.
   */
  isRegression(threshold = 0.02): boolean {
    const f1Delta = this.overall.f1 ?? 0;
    return f1Delta < -threshold - 1e-9;
  }

  /** Return top-N regressions and improvements by |ΔF1|. */
  topMovers(n = 10, threshold = 0.05): { regressions: Mover[]; improvements: Mover[] } {
    const scored: Mover[] = this.perCaseDeltas.map(([caseId, baselineF1, currentF1]) => ({
      caseId,
      baselineF1,
      currentF1,
      deltaF1: currentF1 - baselineF1,
    }));
    const regressions = scored
      .filter((m) => m.deltaF1 <= -threshold)
      .sort((a, b) => a.deltaF1 - b.deltaF1)
      .slice(0, n);
    const improvements = scored
      .filter((m) => m.deltaF1 >= threshold)
      .sort((a, b) => b.deltaF1 - a.deltaF1)
      .slice(0, n);
    return { regressions, improvements };
  }
}

function metricDelta(baseline: MetricSet, current: MetricSet): Record<MetricKey, number> {
  const out = {} as Record<MetricKey, number>;
  for (const k of METRIC_KEYS) out[k] = current[k] - baseline[k];
  return out;
}

function sliceDelta(
  baseline: Record<string, MetricSet>,
  current: Record<string, MetricSet>,
): Record<string, Record<MetricKey, number>> {
  const keys = new Set([...Object.keys(baseline), ...Object.keys(current)]);
  const sorted = [...keys].sort();
  const out: Record<string, Record<MetricKey, number>> = {};
  for (const k of sorted) {
    out[k] = metricDelta(baseline[k] ?? ZERO_METRIC_SET, current[k] ?? ZERO_METRIC_SET);
  }
  return out;
}

export function computeDelta(baseline: Report, current: Report): Delta {
  const overall = metricDelta(baseline.scorecard.overall, current.scorecard.overall);
  const byDifficulty = sliceDelta(
    baseline.scorecard.by_difficulty ?? {},
    current.scorecard.by_difficulty ?? {},
  );
  const byCategory = sliceDelta(
    baseline.scorecard.by_category ?? {},
    current.scorecard.by_category ?? {},
  );
  const byTag = sliceDelta(
    baseline.scorecard.by_tag ?? {},
    current.scorecard.by_tag ?? {},
  );

  const baselineCases = new Map<string, number>();
  for (const c of baseline.per_case ?? []) baselineCases.set(c.id, c.f1);
  const currentCases = new Map<string, number>();
  for (const c of current.per_case ?? []) currentCases.set(c.id, c.f1);

  const common: string[] = [];
  for (const id of baselineCases.keys()) {
    if (currentCases.has(id)) common.push(id);
  }
  common.sort();
  const perCaseDeltas: Array<readonly [string, number, number]> = common.map(
    (id) => [id, baselineCases.get(id)!, currentCases.get(id)!] as const,
  );

  return new Delta(overall, byDifficulty, byCategory, byTag, perCaseDeltas);
}
