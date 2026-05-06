import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Pair, FieldBreakdown } from "../lib/types";

type Props = { pair: Pair };

const renderValue = (v: unknown): string => {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

/** Color scoring: gold for high agreement, fading to ink as score drops.
 *  Mirrors how the human eye reads "this matches" without screaming. */
function scoreClass(score: number): string {
  if (score >= 0.95) return "text-gold-600";
  if (score >= 0.85) return "text-gold-500";
  if (score >= 0.7) return "text-ink-800";
  return "text-ink-500";
}

function diffMark(diff_type: string): string {
  // engine emits "agree" / "disagree" / "different" / "partial"
  const t = diff_type.toLowerCase();
  if (t === "agree" || t === "match") return "=";
  if (t === "partial") return "≈";
  if (t === "disagree" || t === "different") return "≠";
  return "·";
}

export function PairFieldBreakdown({ pair }: Props) {
  const [savedLabel, setSavedLabel] = useState<string | null>(null);
  const [mirrorWarning, setMirrorWarning] = useState<string | null>(null);
  const [open, setOpen] = useState(true); // expanded by default — this IS the content

  const mutation = useMutation({
    mutationFn: (label: "match" | "non_match") =>
      api.postLabel({
        row_id_a: pair.row_id_a,
        row_id_b: pair.row_id_b,
        label,
      }),
    onSuccess: (data, variables) => {
      setSavedLabel(variables);
      setTimeout(() => setSavedLabel(null), 1200);
      // Surface mirror-fall-through: label landed in labels.jsonl but the
      // MemoryStore mirror failed → pipeline won't pick up the decision.
      if (data && data.mirrored === false) {
        setMirrorWarning(
          data.mirror_error ?? "memory mirror failed — label won't apply on next run",
        );
      } else {
        setMirrorWarning(null);
      }
    },
  });

  return (
    <article className="card mb-3">
      {/* header bar */}
      <header className="px-4 py-2.5 border-b border-ink-200 flex items-center gap-4">
        <span className="num text-[13px] text-ink-700 tabular-nums tracking-tightish">
          #{pair.row_id_a}
          <span className="mx-1.5 text-gold-500">→</span>
          #{pair.row_id_b}
        </span>
        <span className="ml-auto flex items-baseline gap-2">
          <span className="eyebrow">score</span>
          <span
            className={`num text-base tabular-nums ${scoreClass(pair.score)}`}
          >
            {pair.score.toFixed(3)}
          </span>
        </span>
      </header>

      {/* prose summary — one-line explanation of why this pair matched */}
      {pair.prose && (
        <p className="px-4 py-2 text-[13px] text-ink-700 italic border-b border-ink-200/60">
          {pair.prose}
        </p>
      )}

      {/* field-by-field diff */}
      {open && (
        <ul className="divide-y divide-ink-200/60">
          {pair.fields.map((f, i) => (
            <FieldRow key={i} field={f} />
          ))}
        </ul>
      )}

      {/* footer: collapse + label actions */}
      <footer className="px-4 py-2.5 border-t border-ink-200 flex items-center gap-3">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="btn-ghost btn !py-1 !px-2 !text-[11px] !uppercase tracking-eyebrow"
        >
          {open ? "collapse" : "expand"} fields
        </button>

        <div className="ml-auto flex items-center gap-2">
          {mirrorWarning && (
            <span
              className="text-[11px] text-amber-700 font-mono"
              title={mirrorWarning}
            >
              ⚠ memory mirror failed
            </span>
          )}
          {mutation.error && (
            <span className="text-xs text-red-700 font-mono">
              {String(mutation.error)}
            </span>
          )}
          <button
            type="button"
            onClick={() => mutation.mutate("match")}
            disabled={mutation.isPending}
            className={
              "btn !py-1 !text-[11px] !uppercase tracking-eyebrow " +
              (savedLabel === "match" ? "border-gold-400 text-gold-600" : "")
            }
          >
            {savedLabel === "match" ? "✓ saved" : "label match"}
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate("non_match")}
            disabled={mutation.isPending}
            className={
              "btn !py-1 !text-[11px] !uppercase tracking-eyebrow " +
              (savedLabel === "non_match"
                ? "border-gold-400 text-gold-600"
                : "")
            }
          >
            {savedLabel === "non_match" ? "✓ saved" : "label non-match"}
          </button>
        </div>
      </footer>
    </article>
  );
}

function FieldRow({ field: f }: { field: FieldBreakdown }) {
  return (
    <li className="grid grid-cols-[10rem_1fr_auto_1fr] items-stretch gap-0">
      {/* Left: field name + scorer + diff mark */}
      <div className="px-4 py-3 border-r border-ink-200/60 flex flex-col justify-center">
        <span className="font-mono text-[13px] text-ink-800">{f.field}</span>
        <span className="num text-[11px] text-ink-400 mt-0.5">
          {f.scorer}
          {" · "}w&nbsp;{f.weight}
        </span>
      </div>

      {/* value_a */}
      <div className="px-4 py-3 border-r border-ink-200/60 flex items-center">
        <span className="font-mono text-sm text-ink-800 break-all">
          {renderValue(f.value_a)}
        </span>
      </div>

      {/* score column — narrow, mono, gold */}
      <div className="px-3 py-3 border-r border-ink-200/60 flex flex-col items-center justify-center min-w-[5.5rem]">
        <span
          className={`num text-base tabular-nums ${scoreClass(f.score)}`}
          title={f.diff_type}
        >
          {f.score.toFixed(3)}
        </span>
        <span className="num text-[11px] text-ink-400 mt-0.5">
          {diffMark(f.diff_type)} {f.diff_type}
        </span>
      </div>

      {/* value_b */}
      <div className="px-4 py-3 flex items-center">
        <span className="font-mono text-sm text-ink-800 break-all">
          {renderValue(f.value_b)}
        </span>
      </div>
    </li>
  );
}
