import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ClusterCase, CompareResponse } from "../lib/api";
import type { Project } from "../lib/types";

type CaseKey = "unchanged" | "merged" | "partitioned" | "overlapping";

const CASE_COLOR: Record<CaseKey, string> = {
  unchanged: "bg-ink-300",
  merged: "bg-gold-500",
  partitioned: "bg-red-500",
  overlapping: "bg-amber-500",
};

const CASE_BLURB: Record<CaseKey, string> = {
  unchanged: "Same members in both runs.",
  merged: "Run A's cluster is fully inside a larger Run B cluster.",
  partitioned: "Run A's cluster split into multiple smaller B clusters.",
  overlapping: "Run A's cluster shares members across multiple B clusters that aren't strict subsets.",
};

export function Compare() {
  const project = useQuery<Project>({
    queryKey: ["project"],
    queryFn: api.project,
  });

  const [runA, setRunA] = useState<string>("");
  const [runB, setRunB] = useState<string>("");
  const [filter, setFilter] = useState<CaseKey | "all">("all");

  const runs = project.data?.runs ?? [];

  // Default-pick the two newest runs the first time the project loads.
  if (project.data && runs.length >= 2 && !runA && !runB) {
    setRunA(runs[0]!.run_name);
    setRunB(runs[1]!.run_name);
  }

  const compareMut = useMutation<CompareResponse, Error, void>({
    mutationFn: () => api.compare(runA, runB),
  });

  const result = compareMut.data;

  return (
    <div className="px-8 py-10 max-w-6xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">compare</p>
        <h1 className="display text-3xl text-ink-900">Run-vs-run (CCMS)</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          Classify every cluster from A as unchanged, merged, partitioned, or
          overlapping with respect to B. TWI summarises the whole transformation.
          No ground-truth labels needed — both runs must just cover the same
          row IDs.
        </p>
      </header>

      <section className="card px-5 py-4 mb-8">
        <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-4 items-end">
          <RunPicker
            label="run A (baseline / ER1)"
            value={runA}
            options={runs.map((r) => r.run_name)}
            onChange={setRunA}
          />
          <RunPicker
            label="run B (comparison / ER2)"
            value={runB}
            options={runs.map((r) => r.run_name)}
            onChange={setRunB}
          />
          <button
            className="btn btn-primary"
            disabled={!runA || !runB || runA === runB || compareMut.isPending}
            onClick={() => compareMut.mutate()}
          >
            {compareMut.isPending ? "Comparing…" : "Compare"}
          </button>
        </div>
        {runA && runB && runA === runB && (
          <p className="mt-3 text-xs text-amber-700">
            ↳ Pick two different runs.
          </p>
        )}
        {compareMut.error && (
          <p className="mt-3 text-xs text-red-700 font-mono break-all">
            ↳ {humanizeError(compareMut.error.message)}
          </p>
        )}
      </section>

      {result && <Results result={result} filter={filter} setFilter={setFilter} />}

      {!result && runs.length < 2 && (
        <div className="card px-6 py-10 text-center">
          <p className="display text-2xl text-ink-700">Need two runs to compare.</p>
          <p className="mt-2 text-sm text-ink-500">
            Auto-run twice from the Project page (e.g. with and without LLM
            boost) and come back.
          </p>
        </div>
      )}
    </div>
  );
}

function RunPicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="eyebrow block mb-1">{label}</span>
      <select
        className="w-full bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">— pick a run —</option>
        {options.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
    </label>
  );
}

function Results({
  result,
  filter,
  setFilter,
}: {
  result: CompareResponse;
  filter: CaseKey | "all";
  setFilter: (f: CaseKey | "all") => void;
}) {
  const s = result.summary;
  const cases: CaseKey[] = ["unchanged", "merged", "partitioned", "overlapping"];

  const filtered =
    filter === "all" ? result.cases : result.cases.filter((c) => c.case === filter);

  return (
    <>
      <section className="grid grid-cols-1 md:grid-cols-[2fr_1fr] gap-6 mb-8">
        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">case distribution · cc1 = {s.cc1}</p>
          <CaseBar summary={s} />
          <ul className="mt-4 grid grid-cols-2 gap-3 text-sm">
            {cases.map((k) => (
              <li
                key={k}
                className="flex items-baseline gap-2 cursor-pointer"
                onClick={() => setFilter(filter === k ? "all" : k)}
              >
                <span className={`inline-block h-2 w-2 rounded-sm ${CASE_COLOR[k]}`} />
                <span className="capitalize text-ink-700">{k}</span>
                <span className="num tabular-nums text-ink-900 ml-auto">
                  {s[k]}
                </span>
                <span className="num tabular-nums text-ink-500 text-xs w-12 text-right">
                  {(s[`${k}_pct`] * 100).toFixed(1)}%
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">indices</p>
          <Stat label="TWI (Talburt-Wang)" value={s.twi.toFixed(4)} hint="1.0 = identical" />
          <Stat label="rows compared" value={s.rc.toString()} />
          <Stat label="clusters · A → B" value={`${s.cc1} → ${s.cc2}`} />
          <Stat
            label="singletons · A → B"
            value={`${s.sc1} → ${s.sc2}`}
            hint="cluster size 1"
          />
        </div>
      </section>

      <section className="card px-5 py-4">
        <header className="flex items-baseline justify-between mb-3">
          <p className="eyebrow">
            cases · {filtered.length} {filter === "all" ? "" : `of ${result.cases.length}`}
          </p>
          {filter !== "all" && (
            <button
              className="text-[12px] uppercase tracking-eyebrow text-gold-600 hover:text-gold-500"
              onClick={() => setFilter("all")}
            >
              clear filter ×
            </button>
          )}
        </header>
        {filter !== "all" && (
          <p className="text-xs text-ink-500 mb-3">↳ {CASE_BLURB[filter]}</p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
                <th className="py-2 pr-3">A cluster</th>
                <th className="py-2 pr-3">case</th>
                <th className="py-2 pr-3">members</th>
                <th className="py-2 pr-3">→ B cluster(s)</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <CaseRow key={c.cluster_id} c={c} />
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function CaseBar({ summary }: { summary: CompareResponse["summary"] }) {
  const total = summary.cc1 || 1;
  const cases: CaseKey[] = ["unchanged", "merged", "partitioned", "overlapping"];
  return (
    <div className="h-3 w-full flex rounded overflow-hidden border border-ink-200">
      {cases.map((k) => {
        const pct = (summary[k] / total) * 100;
        if (pct <= 0) return null;
        return (
          <div
            key={k}
            className={CASE_COLOR[k]}
            style={{ width: `${pct}%` }}
            title={`${k}: ${summary[k]} (${pct.toFixed(1)}%)`}
          />
        );
      })}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="mb-3 last:mb-0">
      <p className="eyebrow">{label}</p>
      <p className="num text-xl text-ink-900 tabular-nums">{value}</p>
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

function CaseRow({ c }: { c: ClusterCase }) {
  const dotClass = CASE_COLOR[c.case];
  const er2Entries = Object.entries(c.er2_clusters);
  return (
    <tr className="border-b border-ink-100 last:border-b-0 align-top">
      <td className="py-2 pr-3 font-mono tabular-nums text-ink-700">{c.cluster_id}</td>
      <td className="py-2 pr-3">
        <span className="inline-flex items-center gap-2">
          <span className={`inline-block h-2 w-2 rounded-sm ${dotClass}`} />
          <span className="capitalize text-ink-800">{c.case}</span>
        </span>
      </td>
      <td className="py-2 pr-3 font-mono text-xs text-ink-600">
        [{c.members.slice(0, 8).join(", ")}
        {c.members.length > 8 ? `, +${c.members.length - 8}` : ""}]
      </td>
      <td className="py-2 pr-3 font-mono text-xs text-ink-600">
        {er2Entries.map(([cid, members]) => (
          <div key={cid}>
            {cid}: [{members.slice(0, 6).join(", ")}
            {members.length > 6 ? `, +${members.length - 6}` : ""}]
          </div>
        ))}
      </td>
    </tr>
  );
}

function humanizeError(message: string): string {
  // post() throws "<status> <body>"; for our 400 the body is JSON
  // {"detail": "..."}. Extract the detail when we can; otherwise pass through.
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
