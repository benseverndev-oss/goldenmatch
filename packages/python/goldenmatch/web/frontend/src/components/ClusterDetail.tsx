import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ClusterDetail as ClusterDetailT } from "../lib/types";
import { PairFieldBreakdown } from "./PairFieldBreakdown";

type Props = {
  data: ClusterDetailT;
  /** Required for unmerge actions. Optional so the component can still
   *  render in test fixtures without backend wiring. */
  runName?: string;
  /** Notify the parent when an unmerge succeeds so it can refetch /
   *  clear the selection (the cluster may no longer exist). */
  onUnmerge?: () => void;
};

const renderValue = (v: unknown): string => {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

export function ClusterDetail({ data, runName, onUnmerge }: Props) {
  const allKeys = new Set<string>();
  for (const r of data.rows) for (const k of Object.keys(r.columns)) allKeys.add(k);
  const cols = Array.from(allKeys);
  const canUnmerge = runName != null && data.rows.length > 1;

  const qc = useQueryClient();
  const unmerge = useMutation({
    mutationFn: (vars: { mode: "record" | "cluster"; rowId?: number }) =>
      api.unmerge(runName!, {
        mode: vars.mode,
        cluster_id: data.cluster_id,
        ...(vars.rowId !== undefined ? { row_id: vars.rowId } : {}),
      }),
    onSuccess: () => {
      // The on-disk run was rewritten; invalidate every read of it.
      qc.invalidateQueries({ queryKey: ["clusters", runName] });
      qc.invalidateQueries({ queryKey: ["cluster", runName] });
      qc.invalidateQueries({ queryKey: ["run", runName] });
      qc.invalidateQueries({ queryKey: ["evaluation", runName] });
      qc.invalidateQueries({ queryKey: ["review", runName] });
      onUnmerge?.();
    },
  });

  return (
    <div className="px-5 py-5">
      <header className="mb-5">
        <p className="eyebrow mb-1">cluster</p>
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="display text-2xl text-gold-600">
            #{data.cluster_id}
            <span className="ml-3 text-ink-500 text-sm font-sans">
              {data.rows.length} member{data.rows.length === 1 ? "" : "s"}
              {" · "}
              {data.pairs.length} pair{data.pairs.length === 1 ? "" : "s"}
            </span>
          </h2>
          {canUnmerge && (
            <button
              type="button"
              className="btn btn-ghost !text-[11px] !uppercase tracking-eyebrow hover:!text-red-700 hover:!border-red-300"
              onClick={() => {
                if (
                  confirm(
                    `Shatter cluster #${data.cluster_id} into ${data.rows.length} singletons? Every pair becomes a confirmed non-match in MemoryStore.`,
                  )
                ) {
                  unmerge.mutate({ mode: "cluster" });
                }
              }}
              disabled={unmerge.isPending}
              title="Split this cluster into singletons. Records the decision in MemoryStore so future runs honor it."
            >
              shatter cluster
            </button>
          )}
        </div>
      </header>

      <section className="mb-8">
        <p className="eyebrow mb-2">members</p>
        <ul className="space-y-2">
          {data.rows.map((r) => (
            <li
              key={r.row_id}
              className="card px-4 py-3 grid gap-x-6 gap-y-1.5 grid-cols-[auto_1fr_auto]"
            >
              <span className="num text-[13px] text-ink-500 row-span-full self-start tabular-nums pt-0.5">
                #{r.row_id}
              </span>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 row-span-full">
                {cols.map((c) => (
                  <FieldKV key={c} k={c} v={r.columns[c]} />
                ))}
              </dl>
              {canUnmerge && (
                <button
                  type="button"
                  className="btn btn-ghost !text-[10px] !uppercase tracking-eyebrow self-start hover:!text-red-700 hover:!border-red-300"
                  onClick={() => {
                    if (
                      confirm(
                        `Pull row #${r.row_id} out of cluster #${data.cluster_id}? It becomes a singleton; remaining members re-cluster among themselves.`,
                      )
                    ) {
                      unmerge.mutate({ mode: "record", rowId: r.row_id });
                    }
                  }}
                  disabled={unmerge.isPending}
                  title="Remove this record from the cluster. Records the decision in MemoryStore."
                >
                  remove
                </button>
              )}
            </li>
          ))}
        </ul>
        {unmerge.error && (
          <p className="mt-2 text-xs text-red-700 font-mono">
            ↳ {String(unmerge.error)}
          </p>
        )}
      </section>

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
