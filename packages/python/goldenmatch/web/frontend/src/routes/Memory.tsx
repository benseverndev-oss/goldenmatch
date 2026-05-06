import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  CorrectionsResponse,
  LearnedAdjustment,
  LearnResponse,
  MemoryCorrection,
  MemoryStatsResponse,
} from "../lib/api";

export function Memory() {
  const qc = useQueryClient();
  const corrections = useQuery<CorrectionsResponse>({
    queryKey: ["memory-corrections"],
    queryFn: () => api.memoryCorrections(),
  });
  const stats = useQuery<MemoryStatsResponse>({
    queryKey: ["memory-stats"],
    queryFn: api.memoryStatsApi,
  });

  const learnMut = useMutation<LearnResponse, Error, void>({
    mutationFn: () => api.memoryLearn(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory-stats"] });
    },
  });

  return (
    <div className="px-8 py-10 max-w-6xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">memory</p>
        <h1 className="display text-3xl text-ink-900">Learning store</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          Every label you save mirrors into the engine's Learning Memory.
          The pipeline reads it on every run via{" "}
          <code className="font-mono text-gold-600">apply_corrections</code>.
          Hit Learn after labeling enough pairs to tune thresholds and field weights.
        </p>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
        <Stat label="corrections" value={stats.data?.count.toString() ?? "—"} />
        <Stat
          label="last learn"
          value={
            stats.data?.last_learn_time
              ? shortDate(stats.data.last_learn_time)
              : "never"
          }
        />
        <Stat
          label="adjustments"
          value={stats.data?.adjustments.length.toString() ?? "—"}
        />
        <div className="card px-4 py-3 flex items-center justify-center">
          <button
            className="btn btn-primary !text-xs"
            disabled={learnMut.isPending}
            onClick={() => learnMut.mutate()}
          >
            {learnMut.isPending ? "Learning…" : "Run learn pass"}
          </button>
        </div>
      </section>

      {learnMut.data && <LearnResultsCard data={learnMut.data} />}
      {learnMut.error && (
        <p className="mb-6 text-sm text-red-700 font-mono break-all">
          ↳ {humanizeError(learnMut.error.message)}
        </p>
      )}

      {stats.data && stats.data.adjustments.length > 0 && (
        <section className="card px-5 py-4 mb-8">
          <p className="eyebrow mb-3">stored adjustments</p>
          <AdjustmentsTable adjustments={stats.data.adjustments} />
        </section>
      )}

      <section className="card px-5 py-4">
        <header className="flex items-baseline justify-between mb-3">
          <p className="eyebrow">
            corrections{" "}
            {corrections.data ? `· ${corrections.data.items.length} of ${corrections.data.total}` : ""}
          </p>
          {corrections.data?.truncated && (
            <span className="text-[11px] text-amber-700">
              showing {corrections.data.limit} of {corrections.data.total}
            </span>
          )}
        </header>
        {corrections.isLoading && (
          <p className="text-sm text-ink-500">Loading corrections…</p>
        )}
        {corrections.data && corrections.data.items.length === 0 && (
          <p className="text-sm text-ink-500">
            No corrections yet. Label some pairs in the run inspector and they'll
            mirror here automatically.
          </p>
        )}
        {corrections.data && corrections.data.items.length > 0 && (
          <CorrectionsTable rows={corrections.data.items} />
        )}
      </section>
    </div>
  );
}

function LearnResultsCard({ data }: { data: LearnResponse }) {
  if (data.adjustments.length === 0) {
    return (
      <div className="mb-6 card px-5 py-4 text-sm text-ink-500">
        Learn pass complete — no adjustments produced. Threshold tuning needs
        ≥10 corrections; weight learning ≥50. Keep labeling.
      </div>
    );
  }
  return (
    <div className="mb-6 card px-5 py-4">
      <p className="eyebrow mb-3 text-gold-600">learn pass · {data.adjustments.length} adjustments</p>
      <AdjustmentsTable adjustments={data.adjustments} />
    </div>
  );
}

function AdjustmentsTable({ adjustments }: { adjustments: LearnedAdjustment[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
            <th className="py-2 pr-3">matchkey</th>
            <th className="py-2 pr-3">threshold</th>
            <th className="py-2 pr-3">weights</th>
            <th className="py-2 pr-3">evidence</th>
            <th className="py-2 pr-3">computed</th>
          </tr>
        </thead>
        <tbody>
          {adjustments.map((a, i) => (
            <tr
              key={i}
              className="border-b border-ink-100 last:border-b-0 font-mono tabular-nums"
            >
              <td className="py-2 pr-3 text-ink-800">{a.matchkey_name}</td>
              <td className="py-2 pr-3 text-gold-600">
                {a.threshold !== undefined ? a.threshold.toFixed(3) : "—"}
              </td>
              <td className="py-2 pr-3 text-ink-700">
                {a.weights
                  ? Object.entries(a.weights)
                      .map(([f, w]) => `${f}=${w.toFixed(2)}`)
                      .join(", ")
                  : "—"}
              </td>
              <td className="py-2 pr-3 text-ink-700">{a.evidence_count}</td>
              <td className="py-2 pr-3 text-ink-500">
                {shortDate(a.computed_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CorrectionsTable({ rows }: { rows: MemoryCorrection[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
            <th className="py-2 pr-3">decision</th>
            <th className="py-2 pr-3">pair</th>
            <th className="py-2 pr-3">source</th>
            <th className="py-2 pr-3">trust</th>
            <th className="py-2 pr-3">matchkey</th>
            <th className="py-2 pr-3">when</th>
            <th className="py-2 pr-3">reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr
              key={c.id}
              className="border-b border-ink-100 last:border-b-0 font-mono tabular-nums"
            >
              <td className="py-2 pr-3">
                <DecisionPill decision={c.decision} />
              </td>
              <td className="py-2 pr-3 text-ink-700">
                #{c.id_a} <span className="text-ink-400">·</span> #{c.id_b}
              </td>
              <td className="py-2 pr-3 text-ink-700">{c.source}</td>
              <td className="py-2 pr-3 text-ink-700">{c.trust.toFixed(2)}</td>
              <td className="py-2 pr-3 text-ink-700">{c.matchkey_name ?? "—"}</td>
              <td className="py-2 pr-3 text-ink-500">
                {c.created_at ? shortDate(c.created_at) : "—"}
              </td>
              <td className="py-2 pr-3 text-ink-600 max-w-xs truncate" title={c.reason ?? ""}>
                {c.reason ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DecisionPill({ decision }: { decision: string }) {
  const cls =
    decision === "merge"
      ? "border-gold-400 text-gold-600 bg-gold-100"
      : decision === "reject"
        ? "border-red-300 text-red-700 bg-red-50"
        : "border-ink-300 text-ink-700";
  return (
    <span className={`px-2 py-0.5 border rounded text-[11px] uppercase tracking-eyebrow ${cls}`}>
      {decision}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card px-4 py-3">
      <p className="eyebrow">{label}</p>
      <p className="num text-2xl text-ink-900 tabular-nums">{value}</p>
    </div>
  );
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
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
