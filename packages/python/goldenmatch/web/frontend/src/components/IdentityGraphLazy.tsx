import { lazy, Suspense, type ComponentProps } from "react";
import type { IdentityGraph as IdentityGraphInner } from "./IdentityGraph";

/**
 * Lazy boundary for {@link IdentityGraph}. The inner module statically imports
 * `echarts` (~1 MB / ~477 KB gzip), so importing it eagerly would put echarts
 * in the main bundle even for users who never open a graph view. Splitting it
 * behind `React.lazy` moves echarts into its own chunk that loads only when a
 * graph view actually mounts (the Identities "graph" toggle or the inspector
 * "graph" tab). Consumers import from HERE, not from `./IdentityGraph`.
 */
const LazyIdentityGraph = lazy(() =>
  import("./IdentityGraph").then((m) => ({ default: m.IdentityGraph })),
);

// Re-export the data types (erased at build time — no echarts pulled in).
export type {
  GraphHub,
  GraphNode,
  GraphLink,
  GraphExpansion,
} from "./IdentityGraph";

export function IdentityGraph(props: ComponentProps<typeof IdentityGraphInner>) {
  return (
    <Suspense
      fallback={
        <div className="text-sm text-ink-500 py-10 text-center">Loading graph…</div>
      }
    >
      <LazyIdentityGraph {...props} />
    </Suspense>
  );
}
