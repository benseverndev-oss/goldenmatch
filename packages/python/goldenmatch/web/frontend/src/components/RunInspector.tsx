import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { ClusterTable } from "./ClusterTable";
import { ClusterDetail } from "./ClusterDetail";

export function RunInspector({ name }: { name: string }) {
  const [selected, setSelected] = useState<number | undefined>(undefined);

  const summaries = useQuery({
    queryKey: ["clusters", name],
    queryFn: () => api.clusters(name),
  });
  const detail = useQuery({
    queryKey: ["cluster", name, selected],
    queryFn: () => api.cluster(name, selected!),
    enabled: selected != null,
  });

  return (
    <div className="grid grid-cols-[minmax(20rem,30%)_1fr] h-full">
      <div className="overflow-hidden border-r border-ink-200 flex flex-col">
        {summaries.isLoading && (
          <div className="p-6 text-sm text-ink-500">Loading clusters…</div>
        )}
        {summaries.error && (
          <div className="p-6 text-sm text-red-700 font-mono">
            {String(summaries.error)}
          </div>
        )}
        {summaries.data && (
          <ClusterTable
            clusters={summaries.data.items}
            onSelect={setSelected}
            selectedId={selected}
          />
        )}
      </div>
      <div className="overflow-auto">
        {selected == null && (
          <div className="h-full grid place-items-center px-8">
            <p className="text-sm text-ink-400 max-w-xs text-center">
              Select a cluster on the left to inspect its members and pairs.
            </p>
          </div>
        )}
        {detail.isLoading && (
          <div className="p-6 text-sm text-ink-500">Loading cluster…</div>
        )}
        {detail.error && (
          <div className="p-6 text-sm text-red-700 font-mono">
            {String(detail.error)}
          </div>
        )}
        {detail.data && <ClusterDetail data={detail.data} />}
      </div>
    </div>
  );
}
