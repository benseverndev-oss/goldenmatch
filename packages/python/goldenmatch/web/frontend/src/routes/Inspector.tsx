import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { api } from "../lib/api";
import { ClusterTable } from "../components/ClusterTable";
import { ClusterDetail } from "../components/ClusterDetail";

export function Inspector() {
  const { name } = useParams({ strict: false }) as { name: string };
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
    <div className="grid grid-cols-2 gap-4 p-4 h-[calc(100vh-3.5rem)]">
      <div className="overflow-auto border rounded">
        {summaries.isLoading && (
          <div className="p-4 text-sm text-gray-500">Loading clusters…</div>
        )}
        {summaries.error && (
          <div className="p-4 text-sm text-red-600">
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
      <div className="overflow-auto border rounded">
        {selected == null && (
          <div className="p-4 text-sm text-gray-500">
            Select a cluster on the left.
          </div>
        )}
        {detail.isLoading && (
          <div className="p-4 text-sm text-gray-500">Loading cluster…</div>
        )}
        {detail.error && (
          <div className="p-4 text-sm text-red-600">
            {String(detail.error)}
          </div>
        )}
        {detail.data && <ClusterDetail data={detail.data} />}
      </div>
    </div>
  );
}
