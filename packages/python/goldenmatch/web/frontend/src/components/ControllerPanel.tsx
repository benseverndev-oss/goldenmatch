import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  ControllerColumnPrior,
  ControllerCommittedMatchkey,
  ControllerDecision,
  ControllerError,
  ControllerNegativeEvidence,
  ControllerTelemetry,
} from "../lib/api";

/** Surfaces the AutoConfigController's last-run output (v1.7-v1.12):
 *  stop_reason, ComplexityProfile health, RunHistory decisions, indicator
 *  column priors, and Path Y negative-evidence on the committed config.
 *
 *  Data source: GET /api/v1/controller/telemetry — populated by /autoconfig
 *  and /run?auto_config=true. Polls on `refetchKey` so the workbench can
 *  invalidate after a fresh auto-config / zero-config run.
 */
export function ControllerPanel({ refetchKey }: { refetchKey?: string | number }) {
  const { data, isLoading } = useQuery<ControllerTelemetry>({
    queryKey: ["controller-telemetry", refetchKey ?? ""],
    queryFn: api.controllerTelemetry,
    // Cache-warm on every workbench mount; the user almost always wants the
    // latest after they trigger auto-config.
    staleTime: 0,
  });

  if (isLoading) {
    return (
      <section className="border-t border-ink-200 pt-5 text-xs text-ink-500">
        Loading controller telemetry…
      </section>
    );
  }
  if (!data || !data.available) {
    return (
      <section className="border-t border-ink-200 pt-5">
        <p className="eyebrow mb-2">controller</p>
        <p className="text-xs text-ink-500">
          Run <span className="text-gold-600">Auto-configure</span> or{" "}
          <span className="text-gold-600">Zero-config run</span> to see the
          controller's stop reason, health verdict, and refit decisions here.
        </p>
      </section>
    );
  }

  return (
    <section className="border-t border-ink-200 pt-5 space-y-4">
      <header className="flex items-baseline gap-3">
        <p className="eyebrow">controller</p>
        <HealthBadge verdict={data.health} />
        <StopReasonBadge reason={data.stop_reason} />
        <span className="ml-auto text-[11px] text-ink-400 font-mono">
          {data.source === "run" && data.run_name
            ? `via ${data.run_name}`
            : data.source ?? ""}
          {data.recorded_at ? ` · ${shortTs(data.recorded_at)}` : ""}
        </span>
      </header>

      <CommittedConfig items={data.committed_matchkeys} ne={data.negative_evidence} />

      <ScoringStrip data={data} />

      {data.column_priors.length > 0 && (
        <ColumnPriorsTable priors={data.column_priors} />
      )}

      {data.decisions.length > 0 && (
        <DecisionList decisions={data.decisions} />
      )}

      {data.errors.length > 0 && (
        <ErrorList errors={data.errors} />
      )}
    </section>
  );
}

function HealthBadge({ verdict }: { verdict: ControllerTelemetry["health"] }) {
  if (!verdict) return null;
  const tone =
    verdict === "green"
      ? "bg-emerald-50 text-emerald-800 border-emerald-300"
      : verdict === "yellow"
        ? "bg-amber-50 text-amber-800 border-amber-300"
        : "bg-red-50 text-red-800 border-red-300";
  return (
    <span
      className={`px-2 py-0.5 text-[11px] uppercase tracking-eyebrow rounded border ${tone}`}
      title="Overall ComplexityProfile health verdict. Red = a sub-profile (blocking, scoring, cluster, …) flagged a problem the controller couldn't repair within budget."
    >
      health · {verdict}
    </span>
  );
}

function StopReasonBadge({ reason }: { reason: ControllerTelemetry["stop_reason"] }) {
  if (!reason) return null;
  // Reason → one-line plain-English hover.
  const tooltip: Record<NonNullable<ControllerTelemetry["stop_reason"]>, string> = {
    green: "Iteration produced a GREEN profile — controller is satisfied.",
    converged: "Profile distance to prior iteration fell below epsilon.",
    budget_iterations: "Max-iteration budget reached before reaching GREEN.",
    budget_time: "Wall-clock budget exhausted.",
    policy_satisfied: "Policy returned no refit proposal; current config is acceptable on the non-green profile.",
    policy_no_progress: "Policy proposed the same config twice in a row.",
    oscillating: "Same (config, rule) pair repeated within a 4-iteration window.",
    cancelled: "Run was cancelled (KeyboardInterrupt).",
  };
  return (
    <span
      className="px-2 py-0.5 text-[11px] uppercase tracking-eyebrow rounded border border-ink-200 bg-paper-100 text-ink-700"
      title={tooltip[reason]}
    >
      stop · {reason.replace(/_/g, " ")}
    </span>
  );
}

function CommittedConfig({
  items,
  ne,
}: {
  items: ControllerCommittedMatchkey[];
  ne: ControllerNegativeEvidence[];
}) {
  if (items.length === 0) return null;
  const neByMk = new Map<string, ControllerNegativeEvidence[]>();
  for (const n of ne) {
    const arr = neByMk.get(n.matchkey_name) ?? [];
    arr.push(n);
    neByMk.set(n.matchkey_name, arr);
  }
  return (
    <div>
      <p className="eyebrow mb-2">committed config</p>
      <ul className="space-y-2 text-xs">
        {items.map((mk) => {
          const neFields = neByMk.get(mk.name) ?? [];
          return (
            <li key={mk.name} className="card px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-mono text-ink-800">{mk.name}</span>
                <span className="text-[10px] uppercase tracking-eyebrow text-ink-500">
                  {mk.type ?? "—"}
                </span>
                {mk.threshold !== null && (
                  <span className="text-ink-500">
                    threshold{" "}
                    <span className="font-mono text-ink-700">
                      {mk.threshold.toFixed(2)}
                    </span>
                  </span>
                )}
                {neFields.length > 0 && (
                  <span
                    className="ml-auto px-2 py-0.5 text-[10px] uppercase tracking-eyebrow rounded border border-violet-300 bg-violet-50 text-violet-800"
                    title={`Path Y active — ${neFields.length} negative-evidence field${neFields.length === 1 ? "" : "s"} can demote this matchkey when they disagree.`}
                  >
                    path Y · {neFields.length} NE
                  </span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-ink-600">
                {mk.fields.map((f, i) => (
                  <span key={i} className="font-mono">
                    {f.column ?? "—"}
                    {f.scorer ? (
                      <span className="text-ink-400">·{f.scorer}</span>
                    ) : null}
                    {f.weight != null ? (
                      <span className="text-ink-400">·w{f.weight.toFixed(1)}</span>
                    ) : null}
                  </span>
                ))}
              </div>
              {neFields.length > 0 && (
                <details className="mt-2 text-[11px]">
                  <summary className="cursor-pointer text-violet-700 hover:text-violet-900">
                    show negative-evidence fields
                  </summary>
                  <ul className="mt-1 pl-3 space-y-0.5">
                    {neFields.map((nf, i) => (
                      <li key={i} className="font-mono text-ink-600">
                        {nf.field} · {nf.scorer} · threshold{" "}
                        {nf.threshold.toFixed(2)} · penalty{" "}
                        <span className="text-violet-700">
                          -{nf.penalty.toFixed(2)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ScoringStrip({ data }: { data: ControllerTelemetry }) {
  const cells: { label: string; value: string; hint: string }[] = [];
  if (data.scoring) {
    cells.push({
      label: "pairs",
      value: numShort(data.scoring.n_pairs_scored),
      hint: "Pairs that survived blocking and got scored.",
    });
    cells.push({
      label: "above thr",
      value: pct(data.scoring.mass_above_threshold),
      hint: "Fraction of scored pairs above the match threshold (high = controller is matching aggressively).",
    });
    cells.push({
      label: "borderline",
      value: pct(data.scoring.mass_in_borderline),
      hint: "Fraction in the borderline band — the review queue is sized by this.",
    });
  }
  if (data.blocking) {
    cells.push({
      label: "blocks",
      value: numShort(data.blocking.n_blocks),
      hint: "Number of blocks the chosen blocking strategy produced.",
    });
    cells.push({
      label: "p99 block",
      value: numShort(data.blocking.block_sizes_p99),
      hint: "99th-percentile block size. Large p99 → blocking key is too coarse.",
    });
  }
  if (data.cluster) {
    cells.push({
      label: "clusters",
      value: numShort(data.cluster.n_clusters),
      hint: "Number of clusters produced (single-record clusters included).",
    });
    cells.push({
      label: "transitivity",
      value: pct(data.cluster.transitivity_rate),
      hint: "Fraction of cluster triangles closed — low = clusters are stitched from weak edges.",
    });
  }
  if (data.full_vs_sample_drift !== null) {
    cells.push({
      label: "drift",
      value: data.full_vs_sample_drift.toFixed(2),
      hint: "L1 distance between sample profile (last iteration) and full-data profile (finalize). High = sample wasn't representative.",
    });
  }
  if (data.elapsed_ms !== null) {
    cells.push({
      label: "elapsed",
      value: `${Math.round(data.elapsed_ms)}ms`,
      hint: "Wall-clock time the controller spent iterating.",
    });
  }
  if (cells.length === 0) return null;
  return (
    <div>
      <p className="eyebrow mb-2">complexity profile</p>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
        {cells.map((c) => (
          <div key={c.label} title={c.hint}>
            <p className="eyebrow text-ink-500">{c.label}</p>
            <p className="num tabular-nums text-ink-800">{c.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ColumnPriorsTable({ priors }: { priors: ControllerColumnPrior[] }) {
  return (
    <details>
      <summary className="eyebrow cursor-pointer hover:text-gold-600">
        indicator column priors · {priors.length}
      </summary>
      <p className="mt-1 text-[11px] text-ink-500">
        Per-column identity / corruption signals computed eagerly by the v1.10
        indicator pass. Higher identity = good anchor candidate. Higher
        corruption = consider adding a normalize transform.
      </p>
      <div className="mt-2 overflow-auto max-h-48">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left eyebrow text-ink-500 border-b border-ink-200/60">
              <th className="py-1 pr-3">column</th>
              <th className="py-1 pr-3">identity</th>
              <th className="py-1 pr-3">corruption</th>
            </tr>
          </thead>
          <tbody>
            {priors.map((p) => (
              <tr key={p.column} className="border-b border-ink-100/60 last:border-b-0 font-mono tabular-nums">
                <td className="py-1 pr-3 text-ink-700">{p.column}</td>
                <td className="py-1 pr-3 text-ink-800">{p.identity_score.toFixed(2)}</td>
                <td className="py-1 pr-3 text-ink-800">{p.corruption_score.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function DecisionList({ decisions }: { decisions: ControllerDecision[] }) {
  return (
    <details>
      <summary className="eyebrow cursor-pointer hover:text-gold-600">
        refit decisions · {decisions.length}
      </summary>
      <p className="mt-1 text-[11px] text-ink-500">
        Ordered list of rule firings from the controller's iteration loop. Each
        rule is the controller's response to a non-green sub-profile.
      </p>
      <ol className="mt-2 space-y-1.5 text-xs">
        {decisions.map((d, i) => (
          <li key={i} className="card px-3 py-2">
            <div className="flex flex-wrap items-baseline gap-x-3 text-[11px]">
              <span className="font-mono text-ink-400">iter {d.iteration}</span>
              <span className="font-mono text-gold-600">{d.rule_name}</span>
              <span className="ml-auto text-ink-400 font-mono">
                {d.wall_clock_ms}ms
              </span>
            </div>
            <p className="mt-1 text-ink-700">{d.rationale}</p>
            {Object.keys(d.config_diff).length > 0 && (
              <ul className="mt-1 pl-3 text-[11px] font-mono text-ink-500">
                {Object.entries(d.config_diff).map(([k, v]) => (
                  <li key={k}>
                    <span className="text-ink-700">{k}</span>: {v}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

function ErrorList({ errors }: { errors: ControllerError[] }) {
  return (
    <details open>
      <summary className="eyebrow text-red-700 cursor-pointer">
        iteration errors · {errors.length}
      </summary>
      <ul className="mt-2 space-y-1 text-xs">
        {errors.map((e, i) => (
          <li key={i} className="card px-3 py-2 border-red-300 bg-red-50/40">
            <div className="text-[11px] font-mono text-red-700">
              iter {e.iteration} · {e.exception_type}
            </div>
            <p className="mt-1 text-ink-700 whitespace-pre-wrap font-mono text-[11px]">
              {e.traceback_summary}
            </p>
          </li>
        ))}
      </ul>
    </details>
  );
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function numShort(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function shortTs(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
