import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { EvaluationResponse } from "../lib/api";
import type { Pair } from "../lib/types";
import { PairFieldBreakdown } from "./PairFieldBreakdown";

type Props = { runName: string };

type Bucket = "tp" | "fp_confirmed" | "fp_unlabeled" | "fn";

/** Per-run evaluation: F1/precision/recall using steward labels as the
 *  ground-truth proxy. Surfaces the full band of confusion (TP / FP /
 *  FN lists) so the user can drill from a metric into the actual pair. */
export function RunEvaluation({ runName }: Props) {
  const q = useQuery<EvaluationResponse>({
    queryKey: ["evaluation", runName],
    queryFn: () => api.runEvaluation(runName),
    refetchInterval: 4000,
  });
  const [bucket, setBucket] = useState<Bucket>("tp");

  if (q.isLoading)
    return <div className="p-4 text-sm text-ink-500">Computing…</div>;
  if (q.error)
    return (
      <div className="p-4 text-sm text-red-700 font-mono">{String(q.error)}</div>
    );
  if (!q.data) return null;

  const s = q.data.summary;
  const labelsExist = s.label_counts.total > 0;
  const onlyPositives =
    s.label_counts.positives > 0 && s.label_counts.negatives === 0;
  const onlyNegatives =
    s.label_counts.positives === 0 && s.label_counts.negatives > 0;

  const buckets: Array<{
    key: Bucket;
    label: string;
    count: number;
    tone: "good" | "bad" | "warn" | "muted";
  }> = [
    { key: "tp", label: "true positives", count: s.tp, tone: "good" },
    { key: "fp_confirmed", label: "confirmed FP", count: s.confirmed_fp, tone: "bad" },
    { key: "fp_unlabeled", label: "unlabeled FP", count: s.unlabeled_fp, tone: "muted" },
    { key: "fn", label: "false negatives", count: s.fn, tone: "warn" },
  ];

  const list: Pair[] = q.data[bucket];

  return (
    <div className="flex flex-col h-full">
      {/* Headline metrics */}
      <header className="px-5 py-4 border-b border-ink-200">
        <p className="eyebrow mb-3">evaluation · vs steward labels</p>
        {!labelsExist ? (
          <NoLabelsBanner />
        ) : (
          <>
            <div className="grid grid-cols-3 gap-6">
              <BigMetric label="F1" value={s.f1} highlight />
              <BigMetric label="precision" value={s.precision} />
              <BigMetric label="recall" value={s.recall} />
            </div>
            {(onlyPositives || onlyNegatives) && (
              <p className="mt-3 text-[11px] text-ink-500 border-l-2 border-gold-300 pl-3 max-w-prose">
                Only{" "}
                {onlyPositives ? (
                  <span className="text-gold-600">positives</span>
                ) : (
                  <span className="text-gold-600">negatives</span>
                )}{" "}
                in the label set so far. Numbers improve as you label across
                both decisions — confirmed FP need non-match labels, FN need
                match labels on pairs the engine missed.
              </p>
            )}
          </>
        )}
      </header>

      {/* Bucket selector */}
      <nav className="px-3 py-2 flex items-center gap-1 border-b border-ink-200 text-[11px]">
        {buckets.map((b) => (
          <button
            key={b.key}
            onClick={() => setBucket(b.key)}
            className={
              "px-2.5 py-1 uppercase tracking-eyebrow border rounded-sm transition-colors " +
              (bucket === b.key
                ? "border-gold-400 text-gold-600 bg-paper-100"
                : "border-transparent text-ink-500 hover:text-ink-800 hover:border-ink-200")
            }
          >
            {b.label}
            <span className={"num ml-1.5 tabular-nums " + toneClass(b.tone)}>
              {b.count}
            </span>
          </button>
        ))}
      </nav>

      {/* Bucket contents */}
      <div className="flex-1 overflow-auto px-4 py-4">
        {list.length === 0 ? (
          <div className="h-full grid place-items-center text-center">
            <p className="text-sm text-ink-500 max-w-sm">
              {emptyMessage(bucket)}
            </p>
          </div>
        ) : (
          list.map((p, i) => (
            <PairFieldBreakdown
              key={`${p.row_id_a}-${p.row_id_b}-${i}`}
              pair={p}
            />
          ))
        )}
      </div>
    </div>
  );
}

function NoLabelsBanner() {
  return (
    <div className="border-l-2 border-gold-300 pl-3 max-w-prose">
      <p className="display text-lg text-ink-800">No labels yet.</p>
      <p className="mt-1 text-sm text-ink-500">
        Open the <span className="text-gold-600">review</span> tab and triage
        a few candidate pairs. F1 / precision / recall against your labels
        appears here in real time as you decide.
      </p>
    </div>
  );
}

function BigMetric({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number;
  highlight?: boolean;
}) {
  return (
    <div>
      <p className="eyebrow mb-1">{label}</p>
      <p
        className={
          "num text-3xl tabular-nums " +
          (highlight ? "text-gold-500" : "text-ink-800")
        }
      >
        {value.toFixed(3)}
      </p>
    </div>
  );
}

function toneClass(tone: "good" | "bad" | "warn" | "muted"): string {
  if (tone === "good") return "text-gold-600";
  if (tone === "bad") return "text-red-700";
  if (tone === "warn") return "text-ink-700";
  return "text-ink-400";
}

function emptyMessage(bucket: Bucket): string {
  switch (bucket) {
    case "tp":
      return "No true positives yet — label some predicted pairs as match to populate this list.";
    case "fp_confirmed":
      return "No confirmed false positives — label predicted pairs as non-match to confirm them as wrong.";
    case "fp_unlabeled":
      return "No unlabeled FPs — every predicted pair is either a TP or a confirmed FP.";
    case "fn":
      return "No false negatives — every pair you've labeled match was also predicted.";
  }
}
