import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { ClusterTable } from "./ClusterTable";
import { ClusterDetail } from "./ClusterDetail";
import { RunEvaluation } from "./RunEvaluation";
import { RunLabels } from "./RunLabels";
import { RunReview } from "./RunReview";
import { SplitPane } from "./SplitPane";
import { IdentityGraph, type GraphExpansion } from "./IdentityGraphLazy";

type LeftTab = "clusters" | "graph" | "review" | "labels" | "evaluation";

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

  // Adapter: expand a cluster hub into its member rows + scored pairs, so the
  // whole run reads as one graph that lazy-loads each cluster on click.
  const expandCluster = async (hubId: string): Promise<GraphExpansion> => {
    const cid = Number(hubId.slice(2)); // strip the "c:" hub-id prefix
    const d = await api.cluster(name, cid);
    return {
      nodes: d.rows.map((r) => ({
        id: `r:${r.row_id}`,
        label: rowLabel(r.columns, r.row_id),
        group: String(r.columns["__source__"] ?? "record"),
      })),
      links: d.pairs.map((p) => ({
        source: `r:${p.row_id_a}`,
        target: `r:${p.row_id_b}`,
        value: p.score,
      })),
    };
  };
  const clusterHubs = (summaries.data?.items ?? []).map((c) => ({
    id: `c:${c.cluster_id}`,
    label: `#${c.cluster_id}`,
    size: c.size,
  }));

  const navBar = (
    <nav className="px-2 pt-2 flex items-center gap-0.5 text-[11px] border-b border-ink-200">
      <TabButton active={tab === "clusters"} onClick={() => setTab("clusters")}>
        clusters
      </TabButton>
      <TabButton active={tab === "graph"} onClick={() => setTab("graph")}>
        graph
      </TabButton>
      <TabButton active={tab === "review"} onClick={() => setTab("review")}>
        review
      </TabButton>
      <TabButton active={tab === "labels"} onClick={() => setTab("labels")}>
        labeled
      </TabButton>
      <TabButton active={tab === "evaluation"} onClick={() => setTab("evaluation")}>
        eval
      </TabButton>
    </nav>
  );

  // The force graph wants width, so it takes over the full inspector area
  // rather than living in the narrow left pane.
  if (tab === "graph") {
    return (
      <div className="h-full flex flex-col">
        {navBar}
        <div className="flex-1 min-h-0 overflow-auto p-4">
          {summaries.isLoading && (
            <div className="text-sm text-ink-500">Loading clusters…</div>
          )}
          {summaries.error && (
            <div className="text-sm text-red-700 font-mono">
              {String(summaries.error)}
            </div>
          )}
          {summaries.data && (
            <IdentityGraph
              hubs={clusterHubs}
              hubGroup="cluster"
              expand={expandCluster}
              height={620}
              emptyHint="No clusters in this run."
            />
          )}
        </div>
      </div>
    );
  }

  const left = (
    <div className="h-full flex flex-col">
      {navBar}
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
        {tab === "evaluation" && <RunEvaluation runName={name} />}
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
      {detail.data && (
        <ClusterDetail
          data={detail.data}
          runName={name}
          onUnmerge={() => setSelected(undefined)}
        />
      )}
    </div>
  );

  return (
    <SplitPane storageKey="goldenmatch-ui:inspector-split" defaultPct={32}>
      {left}
      {right}
    </SplitPane>
  );
}

/** Prefer a human-readable column for a row's node label; skip internal
 *  (`__…__`) columns; fall back to the row id. */
function rowLabel(columns: Record<string, unknown>, rowId: number): string {
  for (const key of ["name", "full_name", "email", "company"]) {
    const v = columns[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  for (const [k, v] of Object.entries(columns)) {
    if (k.startsWith("__")) continue;
    if (typeof v === "string" && v.trim()) return v;
    if (typeof v === "number") return String(v);
  }
  return `row ${rowId}`;
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
