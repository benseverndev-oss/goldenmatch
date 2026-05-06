import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { FieldBreakdown, Pair } from "../lib/types";

type Props = { runName: string };

/** Score-band fallback if settings haven't loaded yet. Settings.review_band_*
 *  is the source of truth for "what does the steward want to triage by default". */
const FALLBACK_LO = 0.5;
const FALLBACK_HI = 1.0;

/** Quick-labelling worklist: pairs in the candidate band that you haven't
 *  triaged yet. Skip / Match / Non-match advance to the next pair. */
export function RunReview({ runName }: Props) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [lo, setLo] = useState<number | null>(null);
  const [hi, setHi] = useState<number | null>(null);
  if (lo === null && settings.data) setLo(settings.data.review_band_lo);
  if (hi === null && settings.data) setHi(settings.data.review_band_hi);
  const effectiveLo = lo ?? FALLBACK_LO;
  const effectiveHi = hi ?? FALLBACK_HI;
  const [skipped, setSkipped] = useState<Set<string>>(new Set());

  const queue = useQuery({
    queryKey: ["review", runName, effectiveLo, effectiveHi],
    queryFn: () =>
      api.runReview(runName, { lo: effectiveLo, hi: effectiveHi, limit: 200 }),
  });

  const label = useMutation({
    mutationFn: (vars: {
      pair: Pair;
      decision: "match" | "non_match";
    }) =>
      api.postLabel({
        row_id_a: vars.pair.row_id_a,
        row_id_b: vars.pair.row_id_b,
        label: vars.decision,
      }),
    onSuccess: () => {
      // Backend filters labeled pairs out by default — refetch advances.
      qc.invalidateQueries({ queryKey: ["review", runName] });
      qc.invalidateQueries({ queryKey: ["run-labels", runName] });
    },
  });

  // Filter out client-skipped pairs (skip is local-only — backend doesn't
  // track skips, only labels — so skipped pairs come back on refetch).
  const visible = useMemo(
    () =>
      (queue.data ?? []).filter(
        (p) => !skipped.has(`${p.row_id_a}-${p.row_id_b}`),
      ),
    [queue.data, skipped],
  );

  const current = visible[0];
  const totalForBand = queue.data?.length ?? 0;
  const reviewedSoFar =
    (queue.data?.length ?? 0) - visible.length + (label.data ? 1 : 0);

  const skip = () => {
    if (!current) return;
    setSkipped((s) => {
      const next = new Set(s);
      next.add(`${current.row_id_a}-${current.row_id_b}`);
      return next;
    });
  };

  // Keyboard shortcuts: M = match, N = non-match, S = skip.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!current || label.isPending) return;
      if (e.target instanceof HTMLElement) {
        const tag = e.target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      }
      if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        label.mutate({ pair: current, decision: "match" });
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        label.mutate({ pair: current, decision: "non_match" });
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        skip();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, label]); // eslint-disable-line react-hooks/exhaustive-deps

  if (queue.isLoading)
    return <div className="p-4 text-sm text-ink-500">Loading review queue…</div>;
  if (queue.error)
    return (
      <div className="p-4 text-sm text-red-700 font-mono">{String(queue.error)}</div>
    );

  return (
    <div className="flex flex-col h-full">
      {/* Score band controls */}
      <header className="px-3 py-2 border-b border-ink-200 flex items-center gap-3">
        <span className="eyebrow">band</span>
        <BandInput value={effectiveLo} onChange={setLo} ariaLabel="lower bound" />
        <span className="text-ink-400">—</span>
        <BandInput value={effectiveHi} onChange={setHi} ariaLabel="upper bound" />
        <span className="ml-auto num text-[11px] text-ink-500 tabular-nums">
          {visible.length} pending · {reviewedSoFar} done · {totalForBand} in band
        </span>
      </header>

      {/* The current pair, presented as a focused card. */}
      <div className="flex-1 overflow-auto px-4 py-5">
        {!current ? (
          <EmptyQueue
            isFiltered={
              effectiveLo !== (settings.data?.review_band_lo ?? FALLBACK_LO) ||
              effectiveHi !== (settings.data?.review_band_hi ?? FALLBACK_HI)
            }
            onReset={() => {
              setLo(settings.data?.review_band_lo ?? FALLBACK_LO);
              setHi(settings.data?.review_band_hi ?? FALLBACK_HI);
              setSkipped(new Set());
            }}
          />
        ) : (
          <ReviewCard
            pair={current}
            onMatch={() => label.mutate({ pair: current, decision: "match" })}
            onNonMatch={() =>
              label.mutate({ pair: current, decision: "non_match" })
            }
            onSkip={skip}
            pending={label.isPending}
            error={label.error ? String(label.error) : null}
          />
        )}
      </div>

      <footer className="px-3 py-2 border-t border-ink-200 text-[11px] text-ink-400 flex items-center gap-4">
        <KeyHint k="M">match</KeyHint>
        <KeyHint k="N">non-match</KeyHint>
        <KeyHint k="S">skip</KeyHint>
      </footer>
    </div>
  );
}

function BandInput({
  value,
  onChange,
  ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
}) {
  return (
    <input
      type="number"
      min={0}
      max={1}
      step={0.05}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      aria-label={ariaLabel}
      className="w-16 text-center"
    />
  );
}

function KeyHint({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <kbd className="font-mono text-[10px] uppercase border border-ink-200 bg-paper-100 rounded px-1 py-0.5 text-ink-700">
        {k}
      </kbd>
      <span className="uppercase tracking-eyebrow">{children}</span>
    </span>
  );
}

function EmptyQueue({
  isFiltered,
  onReset,
}: {
  isFiltered: boolean;
  onReset: () => void;
}) {
  return (
    <div className="h-full grid place-items-center text-center">
      <div className="max-w-sm space-y-3">
        <p className="display text-2xl text-ink-700">All caught up.</p>
        <p className="text-sm text-ink-500">
          No unlabeled pairs in this band. Decisions live in{" "}
          <code className="font-mono text-gold-600">labels.jsonl</code> +{" "}
          <code className="font-mono text-gold-600">MemoryStore</code>.
        </p>
        {isFiltered && (
          <button className="btn" onClick={onReset}>
            Reset band to your default
          </button>
        )}
      </div>
    </div>
  );
}

function scoreClass(score: number): string {
  if (score >= 0.95) return "text-gold-500";
  if (score >= 0.85) return "text-gold-600";
  if (score >= 0.7) return "text-ink-700";
  return "text-ink-500";
}

function diffMark(diff_type: string): string {
  const t = diff_type.toLowerCase();
  if (t === "agree" || t === "match") return "=";
  if (t === "partial") return "≈";
  if (t === "disagree" || t === "different") return "≠";
  return "·";
}

function renderValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function ReviewCard({
  pair,
  onMatch,
  onNonMatch,
  onSkip,
  pending,
  error,
}: {
  pair: Pair;
  onMatch: () => void;
  onNonMatch: () => void;
  onSkip: () => void;
  pending: boolean;
  error: string | null;
}) {
  return (
    <article className="card mx-auto max-w-2xl">
      <header className="px-5 py-3 border-b border-ink-200 flex items-baseline gap-4">
        <span className="eyebrow">candidate pair</span>
        <span className="num text-[15px] text-ink-800 tabular-nums">
          #{pair.row_id_a}
          <span className="mx-2 text-gold-500">→</span>
          #{pair.row_id_b}
        </span>
        <span className="ml-auto flex items-baseline gap-2">
          <span className="eyebrow">score</span>
          <span
            className={`num text-xl tabular-nums ${scoreClass(pair.score)}`}
          >
            {pair.score.toFixed(3)}
          </span>
        </span>
      </header>

      <ul className="divide-y divide-ink-200/60">
        {pair.fields.map((f, i) => (
          <FieldRow key={i} f={f} />
        ))}
      </ul>

      <footer className="px-5 py-4 border-t border-ink-200 flex items-center gap-2">
        <button
          className="btn"
          onClick={onSkip}
          disabled={pending}
          title="Skip · S"
        >
          skip
        </button>
        <div className="ml-auto flex gap-2">
          <button
            className="btn"
            onClick={onNonMatch}
            disabled={pending}
            title="Label non-match · N"
          >
            non-match
          </button>
          <button
            className="btn btn-primary"
            onClick={onMatch}
            disabled={pending}
            title="Label match · M"
          >
            match
          </button>
        </div>
      </footer>

      {error && (
        <p className="px-5 py-2 text-xs text-red-700 font-mono border-t border-ink-200">
          ↳ {error}
        </p>
      )}
    </article>
  );
}

function FieldRow({ f }: { f: FieldBreakdown }) {
  return (
    <li className="grid grid-cols-[8rem_1fr_auto_1fr] items-stretch">
      <div className="px-4 py-2.5 border-r border-ink-200/60 flex flex-col justify-center">
        <span className="font-mono text-[12px] text-ink-800">{f.field}</span>
        <span className="num text-[10px] text-ink-500 mt-0.5">
          {f.scorer}
        </span>
      </div>
      <div className="px-4 py-2.5 border-r border-ink-200/60 flex items-center">
        <span className="font-mono text-[13px] text-ink-800 break-all">
          {renderValue(f.value_a)}
        </span>
      </div>
      <div className="px-3 py-2.5 border-r border-ink-200/60 flex flex-col items-center justify-center min-w-[5rem]">
        <span
          className={`num text-[13px] tabular-nums ${scoreClass(f.score)}`}
        >
          {f.score.toFixed(3)}
        </span>
        <span className="num text-[10px] text-ink-500 mt-0.5">
          {diffMark(f.diff_type)} {f.diff_type}
        </span>
      </div>
      <div className="px-4 py-2.5 flex items-center">
        <span className="font-mono text-[13px] text-ink-800 break-all">
          {renderValue(f.value_b)}
        </span>
      </div>
    </li>
  );
}
