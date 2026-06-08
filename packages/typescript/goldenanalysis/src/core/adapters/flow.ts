/**
 * `flow` adapter — a GoldenFlow `TransformResult`-like object → `AnalyzerInput`.
 *
 * Duck-typed: reads `.df` and `.manifest`; imports nothing from goldenflow. Parity
 * with `adapters/flow.py`.
 */

import type { AnalyzerInput, FrameRows } from "../types.js";

function prop(obj: unknown, key: string): unknown {
  return obj !== null && typeof obj === "object" ? (obj as Record<string, unknown>)[key] : undefined;
}

export function flowArtifacts(result: unknown, options: { dataset?: string } = {}): AnalyzerInput {
  const artifacts: Record<string, unknown> = {
    __producer__: "goldenflow",
    manifest: prop(result, "manifest") ?? null,
  };
  const base = { dataset: options.dataset ?? "flow", artifacts };
  const df = prop(result, "df");
  // Build conditionally so we never set `frame: undefined` (exactOptionalPropertyTypes).
  return df !== null && df !== undefined ? { ...base, frame: df as FrameRows } : base;
}
