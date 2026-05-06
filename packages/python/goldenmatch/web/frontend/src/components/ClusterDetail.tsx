import type { ClusterDetail as ClusterDetailT } from "../lib/types";
import { PairFieldBreakdown } from "./PairFieldBreakdown";

type Props = { data: ClusterDetailT };

const renderValue = (v: unknown) => {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

export function ClusterDetail({ data }: Props) {
  const allKeys = new Set<string>();
  for (const r of data.rows) {
    for (const k of Object.keys(r.columns)) allKeys.add(k);
  }
  const cols = Array.from(allKeys);

  return (
    <div className="p-3">
      <h2 className="text-sm font-semibold mb-2">
        Cluster {data.cluster_id} — {data.rows.length} member
        {data.rows.length === 1 ? "" : "s"}
      </h2>
      <div className="overflow-auto border rounded mb-4">
        <table className="w-full text-xs">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-2 py-1 text-left border-b">row_id</th>
              {cols.map((c) => (
                <th key={c} className="px-2 py-1 text-left border-b">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.row_id} className="border-b">
                <td className="px-2 py-1 font-mono">{r.row_id}</td>
                {cols.map((c) => (
                  <td key={c} className="px-2 py-1">
                    {renderValue(r.columns[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="text-sm font-semibold mb-2">
        Pairs ({data.pairs.length})
      </h3>
      {data.pairs.map((p, i) => (
        <PairFieldBreakdown key={`${p.row_id_a}-${p.row_id_b}-${i}`} pair={p} />
      ))}
    </div>
  );
}
