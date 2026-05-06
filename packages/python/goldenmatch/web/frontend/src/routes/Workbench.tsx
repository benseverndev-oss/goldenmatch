import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api } from "../lib/api";
import type { PydanticError, RulesPayload } from "../lib/types";
import { RuleEditor } from "../components/RuleEditor";
import { RunInspector } from "../components/RunInspector";
import { SplitPane } from "../components/SplitPane";

const DEFAULT_SAMPLE_N = 1000;

export function Workbench() {
  const qc = useQueryClient();
  const initial = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [rules, setRules] = useState<RulesPayload | null>(null);
  const [errors, setErrors] = useState<PydanticError[]>([]);
  const [sampleN, setSampleN] = useState<number | null>(null);
  const [seed, setSeed] = useState<number>(0);
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (initial.data && rules == null) setRules(initial.data);
  }, [initial.data, rules]);

  // Seed sample size from persisted settings on first load. Per-session
  // edits (the user typing a different number) win after that.
  if (sampleN === null && settings.data) {
    setSampleN(settings.data.preview_sample_n);
  }
  const effectiveSampleN = sampleN ?? DEFAULT_SAMPLE_N;

  const previewMutation = useMutation({
    mutationFn: async (current: RulesPayload) => {
      await api.putRules(current);
      return api.preview({
        rules: current,
        sample: { n: effectiveSampleN, seed },
      });
    },
    onSuccess: (resp) => {
      setErrors([]);
      setPreviewName(resp.run_name);
      qc.invalidateQueries({ queryKey: ["clusters"] });
    },
    onError: (err: unknown) => {
      const msg = String(err);
      const parsed = parsePydanticError(msg);
      if (parsed) setErrors(parsed);
      else setToast(msg);
    },
  });

  const saveMutation = useMutation({
    mutationFn: async (current: RulesPayload) => {
      await api.putRules(current);
      return api.saveRules();
    },
    onSuccess: (resp) => {
      setErrors([]);
      setToast(`Saved to ${resp.path}`);
    },
    onError: (err: unknown) => {
      const msg = String(err);
      const parsed = parsePydanticError(msg);
      if (parsed) setErrors(parsed);
      else setToast(msg);
    },
  });

  const domainsQ = useQuery({ queryKey: ["domains"], queryFn: api.domains });
  const [domain, setDomain] = useState<string>("");

  const autoConfigMutation = useMutation({
    mutationFn: () => api.autoconfig(domain || undefined),
    onSuccess: (resp) => {
      setErrors([]);
      setRules(resp);
      // Server adopted the suggestion as in-memory rules; sync our /rules query
      // so the user sees the canonical form on next refetch.
      qc.setQueryData(["rules"], resp);
      setToast(
        `Auto-configured from data.csv — ${resp.matchkeys.length} matchkey${resp.matchkeys.length === 1 ? "" : "s"} suggested. Run preview to see results, or tweak.`,
      );
    },
    onError: (err: unknown) => setToast(String(err)),
  });

  const [llmBoost, setLlmBoost] = useState<boolean | null>(null);
  if (llmBoost === null && settings.data) {
    setLlmBoost(settings.data.llm_boost_default);
  }
  const effectiveLlmBoost = llmBoost ?? false;
  const [savedRunName, setSavedRunName] = useState<string | null>(null);
  const runForRealMutation = useMutation({
    mutationFn: (vars: { autoConfig: boolean }) =>
      api.executeRun({
        auto_config: vars.autoConfig,
        llm_boost: effectiveLlmBoost,
        rules: vars.autoConfig ? undefined : rules ?? undefined,
      }),
    onSuccess: (resp) => {
      setErrors([]);
      setSavedRunName(resp.run_name);
      qc.invalidateQueries({ queryKey: ["project"] });
      setToast(
        `Saved run ${resp.run_name} — ${resp.cluster_count} clusters · ${resp.total_pairs} pairs${resp.llm_boost ? " · LLM boost on" : ""}.`,
      );
    },
    onError: (err: unknown) => setToast(String(err)),
  });

  if (initial.isLoading || rules == null) {
    return <div className="p-12 text-sm text-ink-500">Loading rules…</div>;
  }
  if (initial.error) {
    return (
      <div className="p-12 text-sm text-red-700">{String(initial.error)}</div>
    );
  }

  const left = (
    <aside className="h-full overflow-auto px-6 py-6 space-y-6">
      <header>
        <p className="eyebrow mb-1">workbench</p>
        <h1 className="display text-2xl text-ink-900">Rules</h1>
        <p className="mt-1 text-sm text-ink-500">
          Edit, validate, preview against a sampled slice, then save back to
          <code className="ml-1 font-mono text-gold-600">goldenmatch.yml</code>.
        </p>
      </header>

      <div className="flex flex-wrap gap-2">
        <button
          className="btn btn-primary"
          onClick={() => saveMutation.mutate(rules)}
          disabled={saveMutation.isPending}
        >
          {saveMutation.isPending ? "Saving…" : "Save to goldenmatch.yml"}
        </button>
        <button
          className="btn"
          onClick={() => initial.data && setRules(initial.data)}
        >
          Reset to file
        </button>
        <button
          className="btn"
          onClick={() => autoConfigMutation.mutate()}
          disabled={autoConfigMutation.isPending}
          title="Profile data.csv with goldenmatch's auto-configurator and replace the current rules with the suggestion."
        >
          {autoConfigMutation.isPending ? (
            <span className="flex items-center gap-2">
              <span className="inline-block h-2 w-2 rounded-full bg-gold animate-pulse" />
              Profiling data…
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <span aria-hidden className="text-gold-500">✦</span>
              {domain ? `Auto-configure (${domain})` : "Auto-configure from data"}
            </span>
          )}
        </button>
        {domainsQ.data && domainsQ.data.length > 0 && (
          <label className="flex items-center gap-2 text-[12px] text-ink-600">
            <span className="eyebrow">domain</span>
            <select
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="bg-paper-50 border border-ink-200 rounded px-2 py-1 font-mono text-[12px]"
              title="Pin a domain rulebook before auto-configuring."
            >
              <option value="">— auto-detect —</option>
              {domainsQ.data.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name} ({d.signal_count}s · {d.brand_count}b)
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <RuleEditor rules={rules} onChange={setRules} errors={errors} />

      <section className="border-t border-ink-200 pt-5">
        <p className="eyebrow mb-3">preview</p>
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="eyebrow block mb-1">sample size</span>
            <input
              type="number"
              min={1}
              max={10000}
              value={effectiveSampleN}
              onChange={(e) => setSampleN(Number(e.target.value))}
              className="w-24 text-center"
            />
          </label>
          <label className="block">
            <span className="eyebrow block mb-1">seed</span>
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              className="w-20 text-center"
            />
          </label>
          <button
            className="btn btn-primary"
            onClick={() => previewMutation.mutate(rules)}
            disabled={previewMutation.isPending}
          >
            {previewMutation.isPending ? (
              <span className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-paper-50 animate-pulse" />
                Running preview…
              </span>
            ) : (
              "Run preview"
            )}
          </button>
        </div>
      </section>

      <section className="border-t border-ink-200 pt-5">
        <p className="eyebrow mb-2">save run to project</p>
        <p className="text-xs text-ink-500 mb-3">
          Runs the engine on the full data.csv (not sampled) and writes
          lineage + clusters to the project root. Steward labels (from the
          inspector's review tab) are mirrored to{" "}
          <code className="font-mono text-gold-600">MemoryStore</code> and
          applied automatically on every run.
        </p>
        <label className="flex items-center gap-2 text-sm text-ink-700 mb-3">
          <input
            type="checkbox"
            checked={effectiveLlmBoost}
            onChange={(e) => setLlmBoost(e.target.checked)}
          />
          <span>
            <span className="text-ink-800">LLM boost</span>{" "}
            <span className="text-ink-500">
              · second-opinion on borderline pairs (0.75-0.95). Needs
              OPENAI_API_KEY or ANTHROPIC_API_KEY in env.
              {settings.data && (
                <span className="ml-1">
                  Cost cap ${settings.data.llm_max_cost_usd.toFixed(2)} ·{" "}
                  {settings.data.llm_max_calls} calls
                  <span className="ml-1 text-ink-400">
                    (configurable in <a href="/settings" className="underline hover:text-gold-600">Settings</a>)
                  </span>
                </span>
              )}
            </span>
          </span>
        </label>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-primary"
            onClick={() => runForRealMutation.mutate({ autoConfig: false })}
            disabled={runForRealMutation.isPending}
            title="Run the current rules on the full data.csv. Writes a saved run to the project."
          >
            {runForRealMutation.isPending ? (
              <span className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-paper-50 animate-pulse" />
                Running…
              </span>
            ) : (
              "Save run with current rules"
            )}
          </button>
          <button
            className="btn"
            onClick={() => runForRealMutation.mutate({ autoConfig: true })}
            disabled={runForRealMutation.isPending}
            title="Skip the workbench rules — use auto_configure_df to pick a config and run."
          >
            <span className="flex items-center gap-2">
              <span aria-hidden className="text-gold-500">✦</span>
              Zero-config run
            </span>
          </button>
        </div>
        {savedRunName && (
          <div className="mt-3 text-xs">
            <Link
              to="/runs/$name"
              params={{ name: savedRunName }}
              className="font-mono text-gold-600 hover:text-gold-500"
            >
              → open {savedRunName}
            </Link>
          </div>
        )}
      </section>

      {toast && (
        <div className="card px-3 py-2 flex items-start gap-2 text-xs">
          <span className="font-mono text-gold-600 flex-1 break-all">
            {toast}
          </span>
          <button
            className="text-ink-400 hover:text-ink-700 transition-colors"
            onClick={() => setToast(null)}
          >
            dismiss
          </button>
        </div>
      )}
    </aside>
  );

  const right = (
    <section className="h-full overflow-hidden flex flex-col">
      {previewName ? (
        <>
          <div className="px-5 py-3 border-b border-ink-200 flex items-center gap-3 text-xs">
            <span className="eyebrow">preview run</span>
            <span className="font-mono text-gold-600">{previewName}</span>
            <span className="ml-auto text-ink-400">
              in-memory · evicted from registry after 8 previews
            </span>
          </div>
          <div className="flex-1 min-h-0">
            <RunInspector name={previewName} />
          </div>
        </>
      ) : (
        <div className="h-full grid place-items-center px-8">
          <div className="max-w-md text-center space-y-3">
            <p className="display text-3xl text-ink-700">No preview yet.</p>
            <p className="text-sm text-ink-500">
              Adjust rules on the left, then{" "}
              <span className="text-gold-600">Run preview</span>. The result
              lands here as if it were a saved run — same cluster table, same
              pair drilldown, same labeling.
            </p>
          </div>
        </div>
      )}
    </section>
  );

  return (
    <div className="h-[calc(100vh-7rem)]">
      <SplitPane storageKey="goldenmatch-ui:workbench-split" defaultPct={36}>
        {left}
        {right}
      </SplitPane>
    </div>
  );
}

function parsePydanticError(msg: string): PydanticError[] | null {
  const m =
    msg.match(/^Error:\s+(\d+)\s+([\s\S]+)$/) ??
    msg.match(/^(\d+)\s+([\s\S]+)$/);
  if (!m || m[1] !== "422") return null;
  try {
    const body = JSON.parse(m[2] as string);
    if (Array.isArray(body?.detail)) return body.detail as PydanticError[];
  } catch {
    // fall through
  }
  return null;
}
