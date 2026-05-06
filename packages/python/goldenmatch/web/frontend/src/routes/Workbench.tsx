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

  // Seed local rules from server on first load.
  useEffect(() => {
    if (initial.data && rules == null) setRules(initial.data);
  }, [initial.data, rules]);

  const previewMutation = useMutation({
    mutationFn: async (current: RulesPayload) => {
      // Validate via PUT first so 422 lands here (not on the preview call).
      await api.putRules(current);
      return api.preview({ rules: current, sample: { n: sampleN, seed } });
    },
    onSuccess: (resp) => {
      setErrors([]);
      setPreviewName(resp.run_name);
      // Drop the cached pages for any prior preview.
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
    return <div className="p-8 text-sm text-gray-500">Loading rules…</div>;
  }
  if (initial.error) {
    return (
      <div className="p-8 text-sm text-red-600">{String(initial.error)}</div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 p-4 h-[calc(100vh-3.5rem)]">
      <div className="overflow-auto border rounded p-4 space-y-4">
        <div className="flex gap-2 text-sm">
          <button
            className="px-3 py-1 border rounded hover:bg-gray-50"
            onClick={() => saveMutation.mutate(rules)}
            disabled={saveMutation.isPending}
          >
            Save to goldenmatch.yml
          </button>
          <button
            className="px-3 py-1 border rounded hover:bg-gray-50"
            onClick={() => initial.data && setRules(initial.data)}
          >
            Reset to file
          </button>
        </div>

        <RuleEditor rules={rules} onChange={setRules} errors={errors} />

        <div className="border-t pt-4 space-y-2">
          <h3 className="text-sm font-semibold">Preview</h3>
          <div className="flex gap-2 items-end text-sm">
            <label>
              Sample size
              <input
                type="number"
                min={1}
                max={10000}
                value={sampleN}
                onChange={(e) => setSampleN(Number(e.target.value))}
                className="ml-2 w-24 border rounded px-2 py-1"
              />
            </label>
            <label>
              Seed
              <input
                type="number"
                value={seed}
                onChange={(e) => setSeed(Number(e.target.value))}
                className="ml-2 w-20 border rounded px-2 py-1"
              />
            </label>
            <button
              className="px-3 py-1 border rounded bg-blue-50 hover:bg-blue-100"
              onClick={() => previewMutation.mutate(rules)}
              disabled={previewMutation.isPending}
            >
              {previewMutation.isPending ? "Running…" : "Run preview"}
            </button>
          </div>
        </div>

        {toast && (
          <div className="text-xs text-gray-600 border rounded p-2">
            {toast}
            <button
              className="ml-2 underline"
              onClick={() => setToast(null)}
            >
              dismiss
            </button>
          </div>
        )}
      </div>

      <div className="overflow-auto border rounded">
        {previewName ? (
          <div className="h-full p-2">
            <div className="text-xs text-gray-500 px-2 pb-2">
              preview: <span className="font-mono">{previewName}</span>
            </div>
            <RunInspector name={previewName} />
          </div>
        ) : (
          <div className="p-4 text-sm text-gray-500">
            Run a preview to see clusters here.
          </div>
        )}
      </div>
    </div>
  );
}

// Errors come back from api.ts's json() helper as `Error("${status} ${body}")`.
// Stringified that becomes "Error: ${status} ${body}". Pull the JSON tail and
// parse if shape matches; otherwise fall through so the toast surfaces something.
function parsePydanticError(msg: string): PydanticError[] | null {
  const m = msg.match(/^Error:\s+(\d+)\s+([\s\S]+)$/) ??
    msg.match(/^(\d+)\s+([\s\S]+)$/);
  if (!m || m[1] !== "422") return null;
  try {
    const body = JSON.parse(m[2] as string);
    if (Array.isArray(body?.detail)) return body.detail as PydanticError[];
  } catch {
    // not a JSON detail body — fall through to toast
  }
  return null;
}
