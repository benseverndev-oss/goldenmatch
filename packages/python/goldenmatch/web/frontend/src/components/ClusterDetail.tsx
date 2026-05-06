import type { ClusterDetail as ClusterDetailT } from "../lib/types";
import { PairFieldBreakdown } from "./PairFieldBreakdown";

type Props = { data: ClusterDetailT };

const renderValue = (v: unknown): string => {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

export function ClusterDetail({ data }: Props) {
  const allKeys = new Set<string>();
  for (const r of data.rows) for (const k of Object.keys(r.columns)) allKeys.add(k);
  const cols = Array.from(allKeys);

  return (
    <div className="px-5 py-5">
      {/* cluster eyebrow + title */}
      <header className="mb-5">
        <p className="eyebrow mb-1">cluster</p>
        <h2 className="display text-2xl text-gold-600">
          #{data.cluster_id}
          <span className="ml-3 text-ink-500 text-sm font-sans">
            {data.rows.length} member{data.rows.length === 1 ? "" : "s"}
            {" · "}
            {data.pairs.length} pair{data.pairs.length === 1 ? "" : "s"}
          </span>
        </h2>
      </header>

      {/* member rows — rendered as cards, column names as eyebrows */}
      <section className="mb-8">
        <p className="eyebrow mb-2">members</p>
        <ul className="space-y-2">
          {data.rows.map((r) => (
            <li
              key={r.row_id}
              className="card px-4 py-3 grid gap-x-6 gap-y-1.5 grid-cols-[auto_1fr]"
            >
              <span className="num text-[13px] text-ink-500 row-span-full self-start tabular-nums pt-0.5">
                #{r.row_id}
              </span>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
                {cols.map((c) => (
                  <FieldKV key={c} k={c} v={r.columns[c]} />
                ))}
              </dl>
            </li>
          ))}
        </ul>
      </section>

      {/* pairs */}
      <section>
        <p className="eyebrow mb-2">pairs</p>
        {data.pairs.length === 0 ? (
          <div className="card px-4 py-6 text-center text-sm text-ink-400">
            No pairs scored above threshold for this cluster.
          </div>
        ) : (
          data.pairs.map((p, i) => (
            <PairFieldBreakdown
              key={`${p.row_id_a}-${p.row_id_b}-${i}`}
              pair={p}
            />
          ))
        )}
      </section>
    </div>
  );
}

function FieldKV({ k, v }: { k: string; v: unknown }) {
  return (
    <>
      <dt className="eyebrow self-baseline pt-1">{k}</dt>
      <dd className="font-mono text-[13px] text-ink-800 break-all">
        {renderValue(v)}
      </dd>
    </>
  );
}
