import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { PydanticError, RulesPayload } from "../lib/types";
import { RuleEditor } from "../components/RuleEditor";
import { RunInspector } from "../components/RunInspector";

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

  if (initial.isLoading || rules == null) {
    return <div className="p-12 text-sm text-ink-400">Loading rules…</div>;
  }
  if (initial.error) {
    return (
      <div className="p-12 text-sm text-red-400">{String(initial.error)}</div>
    );
  }

  return (
    <div className="grid grid-cols-[minmax(0,28rem)_1fr] gap-0 h-[calc(100vh-7rem)]">
      {/* LEFT: editor */}
      <aside className="overflow-auto border-r border-ink-800 px-6 py-6 space-y-6">
        <header>
          <p className="eyebrow mb-1">workbench</p>
          <h1 className="display text-2xl text-ink-50">Rules</h1>
          <p className="mt-1 text-sm text-ink-400">
            Edit, validate, preview against a sampled slice, then save back to
            <code className="ml-1 font-mono text-gold-200">goldenmatch.yml</code>.
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
        </div>

        <RuleEditor rules={rules} onChange={setRules} errors={errors} />

        {/* Preview controls */}
        <section className="border-t border-ink-800 pt-5">
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
                  <span className="inline-block h-2 w-2 rounded-full bg-ink-900 animate-pulse" />
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
            <span className="font-mono text-gold-200 flex-1 break-all">
              {toast}
            </span>
            <button
              className="text-ink-500 hover:text-ink-200 transition-colors"
              onClick={() => setToast(null)}
            >
              dismiss
            </button>
          </div>
        )}
      </aside>

      {/* RIGHT: preview pane */}
      <section className="overflow-auto">
        {previewName ? (
          <div className="h-full flex flex-col">
            <div className="px-5 py-3 border-b border-ink-800 flex items-center gap-3 text-xs">
              <span className="eyebrow">preview run</span>
              <span className="font-mono text-gold-200">{previewName}</span>
              <span className="ml-auto text-ink-500">
                in-memory · evicted from registry after 8 previews
              </span>
            </div>
            <div className="flex-1 min-h-0">
              <RunInspector name={previewName} />
            </div>
          </div>
        ) : (
          <div className="h-full grid place-items-center px-8">
            <div className="max-w-md text-center space-y-3">
              <p className="display text-3xl text-ink-200">No preview yet.</p>
              <p className="text-sm text-ink-400">
                Adjust rules on the left, then{" "}
                <span className="text-gold-200">Run preview</span>. The result
                lands here as if it were a saved run — same cluster table, same
                pair drilldown, same labeling.
              </p>
            </div>
          </div>
        )}
      </section>
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
