import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

type Props = {
  runName: string;
  onJump: (clusterId: number) => void;
};

/** Quick-glance list of labels for pairs in this run. Click to navigate to
 *  the cluster — the inspector then opens that cluster's drilldown so you
 *  can revisit / change the label. */
export function RunLabels({ runName, onJump }: Props) {
  const q = useQuery({
    queryKey: ["run-labels", runName],
    queryFn: () => api.runLabels(runName),
    refetchInterval: 4000, // labels can change from the cluster detail panel
  });

  if (q.isLoading)
    return <div className="p-4 text-sm text-ink-500">Loading labels…</div>;
  if (q.error)
    return (
      <div className="p-4 text-sm text-red-700 font-mono">{String(q.error)}</div>
    );

  const labels = q.data ?? [];
  if (labels.length === 0) {
    return (
      <div className="px-5 py-8 text-center">
        <p className="display text-lg text-ink-700">No labels in this run.</p>
        <p className="mt-2 text-sm text-ink-500">
          Open a cluster on the left and click <span className="text-gold-600">label match</span> /{" "}
          <span className="text-gold-600">non-match</span> on a pair. The label
          appears here scoped to this run; the underlying record is dataset-level
          (canonical pair) and survives across runs.
        </p>
      </div>
    );
  }

  const matches = labels.filter((l) => l.label === "match").length;
  const nonMatches = labels.length - matches;

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-ink-200 flex items-center gap-3">
        <span className="eyebrow">labels</span>
        <span className="num text-[11px] text-ink-500 tabular-nums">
          {labels.length} total
        </span>
        <span className="ml-auto flex items-center gap-3 text-[11px]">
          <span className="num text-gold-600 tabular-nums">{matches} ✓</span>
          <span className="num text-ink-500 tabular-nums">{nonMatches} ✗</span>
        </span>
      </div>
      <ul className="overflow-auto flex-1 divide-y divide-ink-200/60">
        {labels.map((l, i) => (
          <li key={`${l.row_id_a}-${l.row_id_b}-${i}`}>
            <button
              type="button"
              onClick={() => onJump(l.cluster_id)}
              className="w-full text-left px-3 py-2.5 hover:bg-paper-100/80 hover:gold-edge transition-colors flex items-baseline gap-3"
            >
              <span
                className={
                  "num text-[11px] uppercase tracking-eyebrow tabular-nums w-16 shrink-0 " +
                  (l.label === "match" ? "text-gold-600" : "text-ink-500")
                }
              >
                {l.label === "match" ? "match" : "non-match"}
              </span>
              <span className="num text-[13px] text-ink-800 tabular-nums">
                #{l.row_id_a}
                <span className="mx-1.5 text-gold-600">→</span>
                #{l.row_id_b}
              </span>
              <span className="ml-auto num text-[11px] text-ink-400 tabular-nums">
                cluster #{l.cluster_id}
              </span>
              {l.note && (
                <span className="block text-[11px] text-ink-500 italic truncate max-w-[14rem]">
                  {l.note}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
