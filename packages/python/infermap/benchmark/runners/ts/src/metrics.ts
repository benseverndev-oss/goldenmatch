// Metric computation for the benchmark runner.
//
// All four metrics are pure functions over a MetricInput — no I/O, no state.
// Mirrors infermap_bench/metrics.py. The aggregation convention per
// spec §9 is: F1 micro, top-1/MRR macro, ECE population.
//
// IMPORTANT: any deviation from Python behavior will fail Phase 10
// cross-language parity tests. Keep semantics exact.

export interface Prediction {
  confidence: number;
  correct: boolean;
}

export interface MetricInput {
  sourceFields: string[];
  targetFields: string[];
  expectedMappings: Array<{ source: string; target: string }>;
  expectedUnmappedSource: string[];
  expectedUnmappedTarget: string[];
  actualMappings: Array<{ source: string; target: string; confidence: number }>;
  scoreMatrix: Record<string, Record<string, number>>;
  minConfidence: number;
}

export interface PerCaseCounts {
  tp: number;
  fp: number;
  fn: number;
}

export function topOneAccuracy(inp: MetricInput): number {
  const expected = new Map<string, string | null>();
  for (const m of inp.expectedMappings) expected.set(m.source, m.target);
  for (const u of inp.expectedUnmappedSource) expected.set(u, null);

  const predicted = new Map<string, string>();
  for (const m of inp.actualMappings) predicted.set(m.source, m.target);

  const total = inp.sourceFields.length;
  if (total === 0) return 0;

  let correct = 0;
  for (const src of inp.sourceFields) {
    const pred = predicted.get(src) ?? null;
    const exp = expected.get(src) ?? null;
    if (pred === exp) correct++;
  }
  return correct / total;
}

export function f1PerCase(inp: MetricInput): PerCaseCounts {
  const expectedSet = new Set<string>(
    inp.expectedMappings.map((m) => `${m.source}\x00${m.target}`),
  );
  const predictedSet = new Set<string>(
    inp.actualMappings.map((m) => `${m.source}\x00${m.target}`),
  );

  let tp = 0;
  let fp = 0;
  for (const p of predictedSet) {
    if (expectedSet.has(p)) tp++;
    else fp++;
  }
  let fn = 0;
  for (const e of expectedSet) {
    if (!predictedSet.has(e)) fn++;
  }
  return { tp, fp, fn };
}

export function microF1(counts: Iterable<PerCaseCounts>): number {
  let tp = 0;
  let fp = 0;
  let fn = 0;
  for (const c of counts) {
    tp += c.tp;
    fp += c.fp;
    fn += c.fn;
  }
  if (tp === 0 && fp === 0 && fn === 0) return 1;
  if (tp + fp === 0 || tp + fn === 0) return 0;
  const precision = tp / (tp + fp);
  const recall = tp / (tp + fn);
  if (precision + recall === 0) return 0;
  return (2 * precision * recall) / (precision + recall);
}

export function meanReciprocalRank(inp: MetricInput): number {
  const expected = new Map<string, string>();
  for (const m of inp.expectedMappings) expected.set(m.source, m.target);
  if (expected.size === 0) return 1;

  const reciprocal: number[] = [];
  for (const [src, correctTarget] of expected) {
    const row = inp.scoreMatrix[src];
    if (row === undefined || Object.keys(row).length === 0) {
      reciprocal.push(0);
      continue;
    }
    const ranked = Object.entries(row).sort((a, b) => {
      if (b[1] !== a[1]) return b[1] - a[1];
      return a[0].localeCompare(b[0]);
    });
    let rank = -1;
    for (let i = 0; i < ranked.length; i++) {
      if (ranked[i]![0] === correctTarget) {
        rank = i + 1;
        break;
      }
    }
    if (rank < 0) reciprocal.push(0);
    else reciprocal.push(1 / rank);
  }

  let sum = 0;
  for (const r of reciprocal) sum += r;
  return sum / reciprocal.length;
}

export function expectedCalibrationError(
  predictions: Prediction[],
  numBins = 10,
): number {
  if (predictions.length === 0) return 0;
  const bins: Prediction[][] = Array.from({ length: numBins }, () => []);
  for (const p of predictions) {
    const idx = Math.min(Math.floor(p.confidence * numBins), numBins - 1);
    bins[idx]!.push(p);
  }
  const total = predictions.length;
  let ece = 0;
  for (const bin of bins) {
    if (bin.length === 0) continue;
    let confSum = 0;
    let correctCount = 0;
    for (const p of bin) {
      confSum += p.confidence;
      if (p.correct) correctCount++;
    }
    const binConf = confSum / bin.length;
    const binAcc = correctCount / bin.length;
    const binWeight = bin.length / total;
    ece += binWeight * Math.abs(binConf - binAcc);
  }
  return ece;
}

export function macroMean(values: number[]): number {
  if (values.length === 0) return 0;
  let sum = 0;
  for (const v of values) sum += v;
  return sum / values.length;
}

export function extractPredictions(inp: MetricInput): Prediction[] {
  const expectedSet = new Set<string>(
    inp.expectedMappings.map((m) => `${m.source}\x00${m.target}`),
  );
  const out: Prediction[] = [];
  for (const m of inp.actualMappings) {
    const key = `${m.source}\x00${m.target}`;
    out.push({ confidence: m.confidence, correct: expectedSet.has(key) });
  }
  return out;
}
