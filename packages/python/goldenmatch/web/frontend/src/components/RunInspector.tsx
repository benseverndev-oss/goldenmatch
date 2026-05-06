import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { ClusterTable } from "./ClusterTable";
import { ClusterDetail } from "./ClusterDetail";
import { RunLabels } from "./RunLabels";
import { RunReview } from "./RunReview";
import { SplitPane } from "./SplitPane";

type LeftTab = "clusters" | "review" | "labels";

export function RunInspector({ name }: { name: string }) {
  const [selected, setSelected] = useState<number | undefined>(undefined);
  const [tab, setTab] = useState<LeftTab>("clusters");

  const summaries = useQuery({
    queryKey: ["clusters", name],
    queryFn: () => api.clusters(name),
  });
  const detail = useQuery({
    queryKey: ["cluster", name, selected],
    queryFn: () => api.cluster(name, selected!),
    enabled: selected != null,
  });

  const left = (
    <div className="h-full flex flex-col">
      <nav className="px-2 pt-2 flex items-center gap-0.5 text-[11px] border-b border-ink-200">
        <TabButton active={tab === "clusters"} onClick={() => setTab("clusters")}>
          clusters
        </TabButton>
        <TabButton active={tab === "review"} onClick={() => setTab("review")}>
          review
        </TabButton>
        <TabButton active={tab === "labels"} onClick={() => setTab("labels")}>
          labeled
        </TabButton>
      </nav>
      <div className="flex-1 min-h-0">
        {tab === "clusters" && (
          <>
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
          </>
        )}
        {tab === "review" && <RunReview runName={name} />}
        {tab === "labels" && (
          <RunLabels
            runName={name}
            onJump={(clusterId) => {
              setSelected(clusterId);
              setTab("clusters");
            }}
          />
        )}
      </div>
    </div>
  );

  const right = (
    <div className="h-full overflow-auto">
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
  );

  return (
    <SplitPane storageKey="goldenmatch-ui:inspector-split" defaultPct={32}>
      {left}
      {right}
    </SplitPane>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "px-3 py-1.5 uppercase tracking-eyebrow font-medium border-b-2 -mb-px transition-colors " +
        (active
          ? "text-gold-600 border-gold"
          : "text-ink-500 border-transparent hover:text-gold-600 hover:border-ink-300")
      }
    >
      {children}
    </button>
  );
}
