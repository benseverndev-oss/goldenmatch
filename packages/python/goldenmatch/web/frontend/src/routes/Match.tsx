import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { MatchResponse, MatchedRow, UnmatchedRow } from "../lib/api";

export function Match() {
  const [target, setTarget] = useState<string>("data.csv");
  const [reference, setReference] = useState<string>("reference.csv");
  const [autoConfig, setAutoConfig] = useState<boolean>(false);

  const matchMut = useMutation<MatchResponse, Error, void>({
    mutationFn: () =>
      api.match({
        target_path: target,
        reference_path: reference,
        auto_config: autoConfig,
      }),
  });

  const result = matchMut.data;

  return (
    <div className="px-8 py-10 max-w-6xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">match</p>
        <h1 className="display text-3xl text-ink-900">Target × reference</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          One-to-many match: every target row gets its best reference match
          above the workbench's threshold. Different output shape from dedupe —
          there are no clusters, just a flat target → reference mapping.
        </p>
      </header>

      <section className="card px-5 py-4 mb-8">
        <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-4 items-end">
          <label className="block">
            <span className="eyebrow block mb-1">target path</span>
            <input
              type="text"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="data.csv"
              className="w-full font-mono text-sm"
            />
          </label>
          <label className="block">
            <span className="eyebrow block mb-1">reference path</span>
            <input
              type="text"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="reference.csv"
              className="w-full font-mono text-sm"
            />
          </label>
          <button
            className="btn btn-primary"
            disabled={!reference || matchMut.isPending}
            onClick={() => matchMut.mutate()}
          >
            {matchMut.isPending ? "Matching…" : "Match"}
          </button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={autoConfig}
            onChange={(e) => setAutoConfig(e.target.checked)}
          />
          <span>
            <span className="text-ink-800">auto-configure</span>{" "}
            <span className="text-ink-500">
              · skip the workbench's rules, profile both files instead
            </span>
          </span>
        </label>
        <p className="mt-2 text-[11px] text-ink-500">
          Paths are resolved under the project root. Use the workbench to tune
          rules before matching unless auto-configure is on.
        </p>
        {matchMut.error && (
          <p className="mt-3 text-xs text-red-700 font-mono break-all">
            ↳ {humanizeError(matchMut.error.message)}
          </p>
        )}
      </section>

      {result && <Results result={result} />}
    </div>
  );
}

function Results({ result }: { result: MatchResponse }) {
  const s = result.stats;
  const matchPct = (s.match_rate * 100).toFixed(1);

  return (
    <>
      <section className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
        <Stat label="target total" value={s.target_total.toString()} />
        <Stat label="reference total" value={s.reference_total.toString()} />
        <Stat label="matched targets" value={s.matched_targets.toString()} hint={`${matchPct}%`} />
        <Stat label="matched pairs" value={s.matched_pairs.toString()} />
        <Stat label="unmatched" value={s.unmatched_targets.toString()} />
      </section>

      <section className="card px-5 py-4 mb-6">
        <header className="flex items-baseline justify-between mb-3">
          <p className="eyebrow">matched · top {result.matched.length}</p>
          {result.matched_truncated && (
            <span className="text-[11px] text-amber-700">
              truncated to {result.row_cap} rows
            </span>
          )}
        </header>
        {result.matched.length === 0 ? (
          <p className="text-sm text-ink-500">No matches above threshold.</p>
        ) : (
          <MatchedTable rows={result.matched} />
        )}
      </section>

      {result.unmatched.length > 0 && (
        <section className="card px-5 py-4">
          <header className="flex items-baseline justify-between mb-3">
            <p className="eyebrow">unmatched targets · {result.unmatched.length}</p>
            {result.unmatched_truncated && (
              <span className="text-[11px] text-amber-700">
                truncated to {result.row_cap} rows
              </span>
            )}
          </header>
          <UnmatchedTable rows={result.unmatched} />
        </section>
      )}
    </>
  );
}

function MatchedTable({ rows }: { rows: MatchedRow[] }) {
  // Pull target_/ref_ columns out of the first row so the header is the same
  // for every row (rows can have differing key sets if the engine omits nulls).
  const sample = rows[0]!;
  const targetCols = Object.keys(sample).filter((k) => k.startsWith("target_"));
  const refCols = Object.keys(sample).filter((k) => k.startsWith("ref_"));

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
            <th className="py-2 pr-3">score</th>
            <th className="py-2 pr-3">target row</th>
            {targetCols.map((c) => (
              <th key={c} className="py-2 pr-3">{c.slice(7)}</th>
            ))}
            <th className="py-2 pr-3 text-gold-500">→</th>
            <th className="py-2 pr-3">ref row</th>
            {refCols.map((c) => (
              <th key={c} className="py-2 pr-3">{c.slice(4)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="border-b border-ink-100 last:border-b-0 font-mono tabular-nums"
            >
              <td className="py-2 pr-3 text-gold-600">
                {Number(row.__match_score__).toFixed(3)}
              </td>
              <td className="py-2 pr-3 text-ink-500">#{Number(row.__target_row_id__)}</td>
              {targetCols.map((c) => (
                <td key={c} className="py-2 pr-3 text-ink-800">
                  {renderVal(row[c])}
                </td>
              ))}
              <td className="py-2 pr-3 text-gold-500" aria-hidden>→</td>
              <td className="py-2 pr-3 text-ink-500">#{Number(row.__ref_row_id__)}</td>
              {refCols.map((c) => (
                <td key={c} className="py-2 pr-3 text-ink-800">
                  {renderVal(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UnmatchedTable({ rows }: { rows: UnmatchedRow[] }) {
  const sample = rows[0]!;
  const cols = Object.keys(sample).filter((k) => !k.startsWith("__"));

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left eyebrow text-ink-500 border-b border-ink-200">
            <th className="py-2 pr-3">row</th>
            {cols.map((c) => (
              <th key={c} className="py-2 pr-3">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="border-b border-ink-100 last:border-b-0 font-mono tabular-nums"
            >
              <td className="py-2 pr-3 text-ink-500">#{Number(row.__row_id__)}</td>
              {cols.map((c) => (
                <td key={c} className="py-2 pr-3 text-ink-800">
                  {renderVal(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card px-4 py-3">
      <p className="eyebrow">{label}</p>
      <p className="num text-2xl text-ink-900 tabular-nums">{value}</p>
      {hint && <p className="text-xs text-ink-500 num tabular-nums">{hint}</p>}
    </div>
  );
}

function renderVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
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
