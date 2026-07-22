import { useEffect, useRef, useState, useCallback } from "react";
import * as echarts from "echarts";

/**
 * IdentityGraph — a collapsed-by-default force-directed graph with
 * click-to-expand neighborhoods, shared by two surfaces:
 *
 *   • the Identities page (durable identity store) — hubs are entities,
 *     expanding fetches an entity's records + evidence edges; and
 *   • the run inspector / workbench preview — hubs are clusters, expanding
 *     fetches a cluster's member rows + scored pairs.
 *
 * Both surfaces have the same list→detail API shape, so the component stays
 * data-agnostic: the caller passes the hub list plus a lazy `expand(id)`
 * that returns that hub's nodes + internal links. The component owns the
 * ECharts wiring, the expand/collapse state, per-group coloring, and the
 * hub→member links that make an expansion burst out of its hub. It scales
 * with the hub count, not the total record count — the whole point.
 */

export type GraphHub = { id: string; label: string; size: number };
/** `group` drives node color/legend: the caller's source name, or the
 *  special value "conflict" for records in a conflicts_with edge. */
export type GraphNode = { id: string; label: string; group: string };
export type GraphLink = {
  source: string;
  target: string;
  value?: number | null;
  kind?: string;
};
export type GraphExpansion = { nodes: GraphNode[]; links: GraphLink[] };

type Props = {
  hubs: GraphHub[];
  /** Legend/color name for hub nodes, e.g. "entity" or "cluster". */
  hubGroup: string;
  /** Lazily fetch one hub's nodes + internal links (cached after first call). */
  expand: (id: string) => Promise<GraphExpansion>;
  height?: number;
  emptyHint?: string;
};

// Theme-matched palette (tailwind.config.js tokens). Hub = wordmark gold,
// conflict = warm red; sources cycle a categorical set that reads on cream.
const HUB_COLOR = "#d4a017";
const CONFLICT_COLOR = "#c0392b";
const PALETTE = [
  "#0f9488", "#2f6fb0", "#7c5cc4", "#c2557a",
  "#3f8f3f", "#b0721f", "#5b6b8c", "#8a4fa0",
];
const INK = "#534b39"; // ink-600, for chart text
const MUTED = "#7a6f55"; // ink-500

function hubSymbolSize(n: number): number {
  if (n <= 1) return 14;
  if (n === 2) return 20;
  if (n === 3) return 26;
  if (n <= 6) return 32;
  return 40;
}

export function IdentityGraph({ hubs, hubGroup, expand, height = 560, emptyHint }: Props) {
  const elRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const cacheRef = useRef<Map<string, GraphExpansion>>(new Map());
  // Stable per-group color assignment (first-seen order), so colors don't
  // reshuffle as new sources appear on expansion.
  const groupColorRef = useRef<Map<string, string>>(new Map());
  const groupSeq = useRef(0);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [showLabels, setShowLabels] = useState(false);
  const [, force] = useState(0);

  const colorForGroup = useCallback(
    (g: string): string => {
      if (g === hubGroup) return HUB_COLOR;
      if (g === "conflict") return CONFLICT_COLOR;
      const seen = groupColorRef.current.get(g);
      if (seen) return seen;
      const c = PALETTE[groupSeq.current % PALETTE.length]!;
      groupSeq.current += 1;
      groupColorRef.current.set(g, c);
      return c;
    },
    [hubGroup],
  );

  const toggle = useCallback(
    async (id: string) => {
      if (expanded.has(id)) {
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        return;
      }
      setExpanded((prev) => new Set(prev).add(id));
      if (!cacheRef.current.has(id)) {
        setLoading((prev) => new Set(prev).add(id));
        try {
          cacheRef.current.set(id, await expand(id));
        } catch {
          cacheRef.current.set(id, { nodes: [], links: [] });
        } finally {
          setLoading((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
          force((n) => n + 1);
        }
      }
    },
    [expanded, expand],
  );

  // Keep the click handler pointing at the latest toggle without re-binding.
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;

  // Init chart once.
  useEffect(() => {
    if (!elRef.current) return;
    const chart = echarts.init(elRef.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    chart.on("click", (p: unknown) => {
      const params = p as { dataType?: string; data?: { id?: string; __hub?: boolean } };
      if (params.dataType === "node" && params.data?.__hub && params.data.id) {
        void toggleRef.current(params.data.id);
      }
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // Rebuild the option whenever inputs change.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    type Item = { id: string; name: string; symbolSize: number; category: number; value: number; __hub?: boolean; label?: unknown };
    const nodes: Item[] = [];
    const links: { source: string; target: string; value: number }[] = [];
    const seen = new Set<string>();

    // Discover the groups present so the legend + category colors are stable.
    const groups: string[] = [hubGroup];
    const groupIndex = new Map<string, number>([[hubGroup, 0]]);
    const groupOf = (g: string) => {
      if (!groupIndex.has(g)) {
        groupIndex.set(g, groups.length);
        groups.push(g);
      }
      return groupIndex.get(g)!;
    };

    for (const h of hubs) {
      if (seen.has(h.id)) continue;
      seen.add(h.id);
      nodes.push({
        id: h.id,
        name: h.label,
        symbolSize: hubSymbolSize(h.size),
        category: 0,
        value: h.size,
        __hub: true,
      });
    }
    for (const id of expanded) {
      const exp = cacheRef.current.get(id);
      if (!exp) continue;
      for (const n of exp.nodes) {
        if (!seen.has(n.id)) {
          seen.add(n.id);
          nodes.push({
            id: n.id,
            name: n.label,
            symbolSize: 13,
            category: groupOf(n.group),
            value: 1,
            label: showLabels ? { show: true, fontSize: 11 } : undefined,
          });
        }
        // Structural member link: hub → its record, so the cluster bursts out.
        links.push({ source: id, target: n.id, value: 0.5 });
      }
      for (const l of exp.links) {
        links.push({ source: l.source, target: l.target, value: l.value ?? 1 });
      }
    }

    const categories = groups.map((name) => ({ name }));
    const colors = groups.map(colorForGroup);

    chart.setOption(
      {
        backgroundColor: "transparent",
        color: colors,
        textStyle: { fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif", color: INK },
        tooltip: {
          trigger: "item",
          confine: true,
          borderColor: "#ece1c0",
          backgroundColor: "#fefcf6",
          textStyle: { color: INK, fontSize: 12 },
          formatter: (p: unknown) => {
            const d = (p as { dataType?: string; data?: Item }).data;
            if (!d || (p as { dataType?: string }).dataType !== "node") return "";
            if (d.__hub) {
              const open = expanded.has(d.id);
              return `<b>${escapeHtml(d.name)}</b><br/>${d.value} record${d.value === 1 ? "" : "s"} · ${open ? "click to collapse" : "click to expand"}`;
            }
            return `<b>${escapeHtml(d.name)}</b><br/><span style="color:${MUTED}">${escapeHtml(d.id)}</span>`;
          },
        },
        legend: [
          {
            data: groups,
            bottom: 4,
            type: "scroll",
            textStyle: { color: MUTED, fontSize: 11 },
            inactiveColor: "#cbbb95",
            itemWidth: 13,
            itemHeight: 8,
          },
        ],
        series: [
          {
            type: "graph",
            layout: "force",
            roam: true,
            categories,
            // Compute the layout up front so hundreds of hubs settle instantly.
            force: { repulsion: 95, gravity: 0.06, edgeLength: 58, friction: 0.2, layoutAnimation: false },
            label: { show: false, color: INK, fontSize: 11, position: "right" },
            emphasis: { focus: "adjacency", label: { show: true } },
            lineStyle: { color: "source", opacity: 0.5, width: 0.9, curveness: 0 },
            data: nodes,
            links,
          },
        ],
      },
      { notMerge: true },
    );
  }, [hubs, expanded, showLabels, hubGroup, colorForGroup]);

  const nRecords = Array.from(expanded).reduce(
    (acc, id) => acc + (cacheRef.current.get(id)?.nodes.length ?? 0),
    0,
  );
  const busy = loading.size > 0;

  return (
    <div>
      <div className="flex items-center gap-3 mb-3 text-[12px] text-ink-500">
        <span className="font-mono">
          {hubs.length} hub{hubs.length === 1 ? "" : "s"}
        </span>
        <span aria-hidden>·</span>
        <span className="font-mono">
          {expanded.size} expanded{nRecords ? ` · ${nRecords} records` : ""}
        </span>
        {busy && <span className="text-gold-600">expanding…</span>}
        <div className="flex-1" />
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showLabels}
            onChange={(e) => setShowLabels(e.target.checked)}
          />
          <span>labels</span>
        </label>
        <button
          className="btn btn-ghost !text-xs !py-1"
          onClick={() => setExpanded(new Set())}
          disabled={expanded.size === 0}
        >
          Collapse all
        </button>
      </div>
      {hubs.length === 0 ? (
        <div className="text-sm text-ink-500 py-10 text-center">
          {emptyHint ?? "Nothing to graph yet."}
        </div>
      ) : (
        <div
          ref={elRef}
          style={{ height, width: "100%" }}
          className="rounded-lg border border-ink-100 bg-paper-50"
        />
      )}
      <p className="mt-2 text-[11px] text-ink-400">
        Click a {hubGroup} to expand its neighborhood · drag / scroll to roam · hover for detail.
      </p>
    </div>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"));
}
