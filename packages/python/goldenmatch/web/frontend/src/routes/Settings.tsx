import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SettingsResponse, WebSettings } from "../lib/api";

export function Settings() {
  const qc = useQueryClient();
  const q = useQuery<SettingsResponse>({
    queryKey: ["settings"],
    queryFn: api.settings,
  });
  const [draft, setDraft] = useState<WebSettings | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    if (q.data && draft == null) {
      const { llm_keys_present, _path, ...editable } = q.data;
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      void llm_keys_present;
      void _path;
      setDraft(editable);
    }
  }, [q.data, draft]);

  const save = useMutation({
    mutationFn: (s: WebSettings) => api.putSettings(s),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setSavedAt(new Date().toLocaleTimeString());
    },
  });

  if (q.isLoading || draft == null)
    return <div className="p-12 text-sm text-ink-500">Loading settings…</div>;
  if (q.error)
    return (
      <div className="p-12 text-sm text-red-700">{String(q.error)}</div>
    );
  if (!q.data) return null;

  const env = q.data.llm_keys_present;
  const hasAnyKey = env.openai || env.anthropic;
  const path = q.data._path;

  const update = <K extends keyof WebSettings>(k: K, v: WebSettings[K]) =>
    setDraft({ ...draft, [k]: v });

  return (
    <div className="px-8 py-10 max-w-3xl mx-auto space-y-10">
      <header>
        <p className="eyebrow mb-2">settings</p>
        <h1 className="display text-3xl text-ink-900">Preferences</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-prose">
          Per-user defaults that survive server restarts. API keys live in
          environment variables (<code className="font-mono text-gold-600">OPENAI_API_KEY</code>{" "}
          / <code className="font-mono text-gold-600">ANTHROPIC_API_KEY</code>) — set
          one in your shell before launching{" "}
          <code className="font-mono text-gold-600">goldenmatch serve-ui</code>.
        </p>
        <p className="mt-2 num text-[11px] text-ink-400 break-all">
          {path}
        </p>
      </header>

      {/* LLM provider */}
      <section className="card px-5 py-4 space-y-4">
        <header className="flex items-baseline justify-between">
          <p className="eyebrow">llm</p>
          <KeyStatus env={env} />
        </header>

        {!hasAnyKey && (
          <p className="text-xs text-ink-500 border-l-2 border-gold-300 pl-3">
            No key in environment. LLM boost will return a 400 if requested.
            Set <code className="font-mono text-gold-600">OPENAI_API_KEY</code>{" "}
            or <code className="font-mono text-gold-600">ANTHROPIC_API_KEY</code>{" "}
            and restart the server.
          </p>
        )}

        <Row label="default LLM boost" hint="Whether the workbench / home toggles default to on">
          <input
            type="checkbox"
            checked={draft.llm_boost_default}
            onChange={(e) => update("llm_boost_default", e.target.checked)}
          />
        </Row>

        <Row label="provider" hint="Which key to prefer when both are set">
          <select
            value={draft.llm_provider}
            onChange={(e) => update("llm_provider", e.target.value as "openai" | "anthropic")}
            className="w-32"
          >
            <option value="openai">openai</option>
            <option value="anthropic">anthropic</option>
          </select>
        </Row>

        <Row label="max cost per run" hint="USD ceiling — pipeline stops calling the LLM when reached">
          <span className="flex items-center gap-1">
            <span className="num text-ink-500">$</span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.01}
              value={draft.llm_max_cost_usd}
              onChange={(e) => update("llm_max_cost_usd", Number(e.target.value))}
              className="w-24 text-right"
            />
          </span>
        </Row>

        <Row label="max calls per run" hint="Backstop: stop calling once this many requests have happened">
          <input
            type="number"
            min={1}
            max={100000}
            step={50}
            value={draft.llm_max_calls}
            onChange={(e) => update("llm_max_calls", Number(e.target.value))}
            className="w-28 text-right"
          />
        </Row>
      </section>

      {/* Review queue band */}
      <section className="card px-5 py-4 space-y-4">
        <p className="eyebrow">review queue</p>
        <p className="text-xs text-ink-500">
          Default score band the inspector's review tab uses to surface
          candidate pairs.
        </p>
        <div className="flex items-center gap-3">
          <Row label="lower bound" hint="">
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={draft.review_band_lo}
              onChange={(e) => update("review_band_lo", Number(e.target.value))}
              className="w-24 text-right"
            />
          </Row>
          <Row label="upper bound" hint="">
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={draft.review_band_hi}
              onChange={(e) => update("review_band_hi", Number(e.target.value))}
              className="w-24 text-right"
            />
          </Row>
        </div>
      </section>

      {/* Preview */}
      <section className="card px-5 py-4 space-y-3">
        <p className="eyebrow">preview</p>
        <Row label="default sample size" hint="Used when the workbench preview opens">
          <input
            type="number"
            min={1}
            max={10000}
            step={100}
            value={draft.preview_sample_n}
            onChange={(e) => update("preview_sample_n", Number(e.target.value))}
            className="w-28 text-right"
          />
        </Row>
      </section>

      <footer className="flex items-center gap-3">
        <button
          className="btn btn-primary"
          onClick={() => save.mutate(draft)}
          disabled={save.isPending}
        >
          {save.isPending ? "Saving…" : "Save settings"}
        </button>
        <button
          className="btn"
          onClick={() => {
            if (q.data) {
              const { llm_keys_present, _path, ...editable } = q.data;
              void llm_keys_present;
              void _path;
              setDraft(editable);
            }
          }}
        >
          Discard changes
        </button>
        {save.error && (
          <span className="text-xs text-red-700 font-mono">
            ↳ {String(save.error)}
          </span>
        )}
        {savedAt && !save.isPending && (
          <span className="text-xs text-ink-500">Saved at {savedAt}</span>
        )}
      </footer>
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-4">
      <div className="w-44 shrink-0">
        <p className="eyebrow">{label}</p>
        {hint && <p className="text-[11px] text-ink-400 mt-0.5">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function KeyStatus({
  env,
}: {
  env: { openai: boolean; anthropic: boolean };
}) {
  return (
    <span className="flex items-center gap-3 text-[11px]">
      <KeyDot label="openai" present={env.openai} />
      <KeyDot label="anthropic" present={env.anthropic} />
    </span>
  );
}

function KeyDot({ label, present }: { label: string; present: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 uppercase tracking-eyebrow text-ink-500">
      <span
        aria-hidden
        className={
          "inline-block h-1.5 w-1.5 rounded-full " +
          (present ? "bg-gold" : "bg-ink-300")
        }
      />
      {label}
      <span className="num text-ink-400">
        {present ? "ready" : "—"}
      </span>
    </span>
  );
}
