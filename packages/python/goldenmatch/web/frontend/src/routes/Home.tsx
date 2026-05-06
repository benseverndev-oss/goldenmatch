import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api } from "../lib/api";
import type { Project } from "../lib/types";

export function Home() {
  const qc = useQueryClient();
  const [llmBoost, setLlmBoost] = useState<boolean | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  // Seed the local llmBoost toggle from persisted settings.llm_boost_default
  // on first load. After that the user's per-session toggle wins.
  if (llmBoost === null && settings.data) {
    setLlmBoost(settings.data.llm_boost_default);
  }
  const effectiveLlmBoost = llmBoost ?? false;

  const { data, isLoading, error } = useQuery<Project>({
    queryKey: ["project"],
    queryFn: api.project,
  });

  const autoRunMutation = useMutation({
    mutationFn: () =>
      api.executeRun({ auto_config: true, llm_boost: effectiveLlmBoost }),
    onSuccess: () => {
      setRunError(null);
      qc.invalidateQueries({ queryKey: ["project"] });
    },
    onError: (err: unknown) => setRunError(String(err)),
  });

  if (isLoading)
    return <div className="p-12 text-sm text-ink-500">Loading project…</div>;
  if (error)
    return (
      <div className="p-12 text-sm text-red-700">
        Failed to load: {String(error)}
      </div>
    );
  if (!data) return null;

  const ruleSummary = data.rules.matchkeys.length
    ? `${data.rules.matchkeys.length} matchkey${data.rules.matchkeys.length === 1 ? "" : "s"} · threshold ${data.rules.threshold.toFixed(2)}`
    : "no matchkeys configured";

  return (
    <div className="px-8 py-10 max-w-5xl mx-auto">
      <section className="mb-10">
        <p className="eyebrow mb-2">project</p>
        <h1 className="display text-4xl text-ink-900 break-all">
          {data.project_root}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className="text-ink-500">
            <span className="eyebrow mr-2">config</span>
            <span className="font-mono text-ink-700">
              {data.config_path ? trimPath(data.config_path) : "—"}
            </span>
          </span>
          <span className="text-ink-500">
            <span className="eyebrow mr-2">rules</span>
            <span className="text-ink-700">{ruleSummary}</span>
          </span>
        </div>
      </section>

      <section className="mb-10 card px-5 py-4">
        <div className="flex items-baseline gap-3 mb-2">
          <p className="eyebrow">auto-run</p>
          <p className="text-xs text-ink-500">
            Skip the workbench — let goldenmatch profile data.csv, pick a
            config, and write a saved run. Add LLM boost for borderline pairs.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            className="btn btn-primary"
            onClick={() => autoRunMutation.mutate()}
            disabled={autoRunMutation.isPending}
          >
            {autoRunMutation.isPending ? (
              <span className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-paper-50 animate-pulse" />
                Running…
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <span aria-hidden>✦</span>
                {llmBoost ? "Auto-run with LLM boost" : "Auto-run"}
              </span>
            )}
          </button>
          <label className="flex items-center gap-2 text-sm text-ink-700">
            <input
              type="checkbox"
              checked={effectiveLlmBoost}
              onChange={(e) => setLlmBoost(e.target.checked)}
            />
            <span>
              <span className="text-ink-800">LLM boost</span>{" "}
              <span className="text-ink-500">
                · review-band pairs get a second-opinion call
                {settings.data && !settings.data.llm_keys_present.openai &&
                 !settings.data.llm_keys_present.anthropic &&
                  " · no API key in env"}
              </span>
            </span>
          </label>
          {autoRunMutation.data && (
            <Link
              to="/runs/$name"
              params={{ name: autoRunMutation.data.run_name }}
              className="ml-auto text-xs font-mono text-gold-600 hover:text-gold-500"
            >
              → open {autoRunMutation.data.run_name}
            </Link>
          )}
        </div>
        {runError && (
          <p className="mt-2 text-xs text-red-700 font-mono">↳ {runError}</p>
        )}
      </section>

      <section>
        <header className="flex items-baseline justify-between mb-4">
          <h2 className="eyebrow">runs · {data.runs.length}</h2>
          <Link
            to="/workbench"
            className="text-[12px] uppercase tracking-eyebrow text-ink-500 hover:text-gold-600 transition-colors"
          >
            open workbench →
          </Link>
        </header>

        {data.runs.length === 0 ? (
          <EmptyRuns />
        ) : (
          <ol className="space-y-3">
            {data.runs.map((r, idx) => (
              <li key={r.run_name}>
                <Link
                  to="/runs/$name"
                  params={{ name: r.run_name }}
                  className="card group block px-5 py-4 hover:gold-edge hover:border-ink-600 transition-all"
                >
                  <div className="flex items-baseline justify-between gap-6">
                    <div className="flex items-baseline gap-4">
                      <span className="num text-[13px] text-ink-400 tabular-nums">
                        {String(idx + 1).padStart(2, "0")}
                      </span>
                      <h3 className="display text-xl text-ink-900 group-hover:text-gold-600 transition-colors">
                        {r.run_name}
                      </h3>
                    </div>
                    <Stat label="generated" value={shortDate(r.generated_at)} />
                  </div>
                  <dl className="mt-4 grid grid-cols-3 gap-6 max-w-md">
                    <Metric label="rows" value={r.row_count} />
                    <Metric label="clusters" value={r.cluster_count} />
                    <Metric label="pairs" value={r.total_pairs} />
                  </dl>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="eyebrow mb-1">{label}</p>
      <p className="num text-2xl text-ink-800 tabular-nums">{value}</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <p className="eyebrow">{label}</p>
      <p className="num text-[13px] text-ink-600 mt-0.5">{value}</p>
    </div>
  );
}

function EmptyRuns() {
  return (
    <div className="card px-6 py-10 text-center">
      <p className="display text-2xl text-ink-700">No runs in this project.</p>
      <p className="mt-2 text-sm text-ink-500">
        Drop a <code className="font-mono text-gold-600">*_lineage.json</code> +{" "}
        <code className="font-mono text-gold-600">*_clusters.csv</code> pair
        next to <code className="font-mono text-gold-600">data.csv</code>, or
        head to the workbench and run a preview.
      </p>
    </div>
  );
}

function trimPath(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean);
  if (parts.length <= 3) return path;
  return "…/" + parts.slice(-3).join("/");
}

function shortDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
