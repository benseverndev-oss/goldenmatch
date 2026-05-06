import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SensitivityPoint, SensitivityResponse } from "../lib/api";
import type { RulesPayload } from "../lib/types";

const PRESET_FIELDS = [
  { value: "threshold", label: "threshold (all fuzzy matchkeys)" },
  { value: "blocking.max_block_size", label: "blocking · max_block_size" },
];

export function Sensitivity() {
  const rulesQ = useQuery<RulesPayload>({
    queryKey: ["rules"],
    queryFn: api.rules,
  });

  const [field, setField] = useState<string>("threshold");
  const [start, setStart] = useState<number>(0.7);
  const [stop, setStop] = useState<number>(0.95);
  const [step, setStep] = useState<number>(0.05);
  const [sampleN, setSampleN] = useState<number>(500);

  const matchkeyFields = (rulesQ.data?.matchkeys ?? [])
    .map((m) => m.column)
    .filter(Boolean)
    .map((col) => ({
      value: `matchkey.fuzzy_${col}.threshold`,
      label: `matchkey · fuzzy_${col} · threshold`,
    }));
  const fieldOptions = [...PRESET_FIELDS, ...matchkeyFields];

  const sweepMut = useMutation<SensitivityResponse, Error, void>({
    mutationFn: () =>
      api.sensitivity({ field, start, stop, step, sample_n: sampleN }),
  });

  const result = sweepMut.data;

  return (
    <div className="px-8 py-10 max-w-6xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">sensitivity</p>
        <h1 className="display text-3xl text-ink-900">Parameter sweep</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          Re-run the pipeline at each value, CCMS-compare the result against a
          baseline, and chart how the clustering shifts. Useful for spotting
          plateaus where a knob barely matters and edges where it falls off.
        </p>
      </header>

      <section className="card px-5 py-4 mb-8">
        <div className="grid grid-cols-1 md:grid-cols-[2fr_repeat(4,1fr)_auto] gap-3 items-end">
          <label className="block">
            <span className="eyebrow block mb-1">parameter</span>
            <select
              className="w-full bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-sm"
              value={field}
              onChange={(e) => setField(e.target.value)}
            >
              {fieldOptions.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <NumField label="start" value={start} step={0.01} onChange={setStart} />
          <NumField label="stop" value={stop} step={0.01} onChange={setStop} />
          <NumField label="step" value={step} step={0.01} onChange={setStep} min={0.001} />
          <NumField
            label="sample n"
            value={sampleN}
            step={100}
            onChange={(v) => setSampleN(Math.max(10, Math.round(v)))}
            min={10}
          />
          <button
            className="btn btn-primary"
            disabled={sweepMut.isPending || stop <= start || step <= 0}
            onClick={() => sweepMut.mutate()}
          >
            {sweepMut.isPending ? "Sweeping…" : "Sweep"}
          </button>
        </div>
        {sweepMut.error && (
          <p className="mt-3 text-xs text-red-700 font-mono break-all">
            ↳ {humanizeError(sweepMut.error.message)}
          </p>
        )}
        <p className="mt-3 text-[11px] text-ink-500">
          Each sweep runs the pipeline {estimatePoints(start, stop, step) + 1} times on a {sampleN}-row sample (1 baseline + {estimatePoints(start, stop, step)} points).
        </p>
      </section>

      {result && <Results result={result} />}
    </div>
  );
}

function Results({ result }: { result: SensitivityResponse }) {
  if (result.points.length === 0) {
    return (
      <div className="card px-6 py-10 text-center">
        <p className="display text-2xl text-ink-700">No points returned.</p>
        <p className="mt-2 text-sm text-ink-500">
          Every sweep value failed — widen the range or check the field name.
        </p>
      </div>
    );
  }

  const ccs = result.points.map((p) => p.cluster_count_b);
  const minCC = Math.min(...ccs);
  const maxCC = Math.max(...ccs);
  const baselineCC = result.points[0]?.cluster_count_a ?? null;

  return (
    <>
      <section className="grid grid-cols-1 md:grid-cols-[2fr_1fr] gap-6 mb-8">
        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">cluster count vs {result.field}</p>
          <Sparkline points={result.points} minCC={minCC} maxCC={maxCC} />
          <div className="mt-3 flex justify-between text-[11px] text-ink-500 font-mono tabular-nums">
            <span>{result.points[0]?.value.toFixed(3)}</span>
            <span className="text-ink-400">
              cluster count · {minCC} → {maxCC}
            </span>
            <span>{result.points[result.points.length - 1]?.value.toFixed(3)}</span>
          </div>
        </div>

        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">stability</p>
          <Stat
            label="baseline"
            value={result.baseline_value?.toFixed(4) ?? "—"}
            hint={baselineCC !== null ? `${baselineCC} clusters` : undefined}
          />
          <Stat
            label="most stable value"
            value={result.stability.best_value.toFixed(4)}
            hint={`${(result.stability.best_unchanged_pct * 100).toFixed(1)}% unchanged`}
          />
          <Stat label="points" value={result.points.length.toString()} />
          <Stat label="sample n" value={result.sample_n.toString()} />
        </div>
      </section>

      <section className="card px-5 py-4">
        <p className="eyebrow mb-3">per-point detail</p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
                <th className="py-2 pr-3">value</th>
                <th className="py-2 pr-3">clusters (B)</th>
                <th className="py-2 pr-3">TWI</th>
                <th className="py-2 pr-3">unchanged</th>
                <th className="py-2 pr-3">merged</th>
                <th className="py-2 pr-3">partitioned</th>
                <th className="py-2 pr-3">overlapping</th>
              </tr>
            </thead>
            <tbody>
              {result.points.map((p) => (
                <tr
                  key={p.value}
                  className="border-b border-ink-100 last:border-b-0 font-mono tabular-nums"
                >
                  <td className="py-2 pr-3 text-ink-800">{p.value.toFixed(4)}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.cluster_count_b}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.twi.toFixed(3)}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.unchanged}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.merged}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.partitioned}</td>
                  <td className="py-2 pr-3 text-ink-700">{p.overlapping}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function Sparkline({
  points,
  minCC,
  maxCC,
}: {
  points: SensitivityPoint[];
  minCC: number;
  maxCC: number;
}) {
  const W = 600;
  const H = 80;
  const pad = 4;
  const span = Math.max(maxCC - minCC, 1);

  const xs = points.map((_, i) =>
    points.length === 1 ? W / 2 : pad + (i / (points.length - 1)) * (W - 2 * pad),
  );
  const ys = points.map(
    (p) => H - pad - ((p.cluster_count_b - minCC) / span) * (H - 2 * pad),
  );
  const path = xs.map((x, i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${ys[i]!.toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20" aria-label="cluster-count sparkline">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gold-500" />
      {xs.map((x, i) => (
        <circle key={i} cx={x} cy={ys[i]} r={2.5} className="fill-gold-500" />
      ))}
    </svg>
  );
}

function NumField({
  label,
  value,
  step,
  onChange,
  min,
}: {
  label: string;
  value: number;
  step: number;
  onChange: (v: number) => void;
  min?: number;
}) {
  return (
    <label className="block">
      <span className="eyebrow block mb-1">{label}</span>
      <input
        type="number"
        className="w-full bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-sm tabular-nums"
        value={value}
        step={step}
        {...(min !== undefined ? { min } : {})}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) onChange(v);
        }}
      />
    </label>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="mb-3 last:mb-0">
      <p className="eyebrow">{label}</p>
      <p className="num text-xl text-ink-900 tabular-nums">{value}</p>
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

function estimatePoints(start: number, stop: number, step: number): number {
  if (step <= 0 || stop <= start) return 0;
  return Math.floor((stop - start) / step) + 1;
}

function humanizeError(message: string): string {
  const match = message.match(/^\d+\s+(\{.*\})$/s);
  if (match) {
    try {
      const body = JSON.parse(match[1]!);
      if (typeof body.detail === "string") return body.detail;
    } catch {
      // ignore
    }
  }
  return message;
}
