import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { PydanticError, RulesPayload } from "../lib/types";
import { RuleEditor } from "../components/RuleEditor";
import { RunInspector } from "../components/RunInspector";
import { SplitPane } from "../components/SplitPane";

const DEFAULT_SAMPLE_N = 1000;

export function Workbench() {
  const qc = useQueryClient();
  const initial = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const [rules, setRules] = useState<RulesPayload | null>(null);
  const [errors, setErrors] = useState<PydanticError[]>([]);
  const [sampleN, setSampleN] = useState<number>(DEFAULT_SAMPLE_N);
  const [seed, setSeed] = useState<number>(0);
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (initial.data && rules == null) setRules(initial.data);
  }, [initial.data, rules]);

  const previewMutation = useMutation({
    mutationFn: async (current: RulesPayload) => {
      await api.putRules(current);
      return api.preview({ rules: current, sample: { n: sampleN, seed } });
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

  const autoConfigMutation = useMutation({
    mutationFn: () => api.autoconfig(),
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
              Auto-configure from data
            </span>
          )}
        </button>
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
              value={sampleN}
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
