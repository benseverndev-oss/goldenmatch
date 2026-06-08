/**
 * `pipe` adapter — a GoldenPipe `PipeResult`-like object → `AnalyzerInput`.
 *
 * Near-passthrough: `result.artifacts` already carries the per-stage outputs
 * (findings / manifest / clusters / scored_pairs / match_stats / recall_certificate)
 * under the same keys the analyzers read. Duck-typed; no goldenpipe import. Parity
 * with `adapters/pipe.py`.
 */

import type { AnalyzerInput } from "../types.js";
import { normalizeCert } from "./match.js";

function prop(obj: unknown, key: string): unknown {
  return obj !== null && typeof obj === "object" ? (obj as Record<string, unknown>)[key] : undefined;
}

/** `Path(source).stem`, or "frame" for empty / non-string / "<...>" sources. */
function datasetFromSource(source: unknown): string {
  if (typeof source !== "string" || source.length === 0 || source.startsWith("<")) return "frame";
  const stem = source.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
  return stem.length > 0 ? stem : "frame";
}

export function pipeArtifacts(result: unknown, options: { dataset?: string } = {}): AnalyzerInput {
  const src = prop(result, "artifacts");
  const artifacts: Record<string, unknown> =
    src !== null && typeof src === "object" ? { ...(src as Record<string, unknown>) } : {};
  artifacts["__producer__"] = "goldenpipe";
  if ("recall_certificate" in artifacts) {
    const normalized = normalizeCert(artifacts["recall_certificate"]);
    if (normalized === null) delete artifacts["recall_certificate"];
    else artifacts["recall_certificate"] = normalized;
  }
  const dataset = options.dataset ?? datasetFromSource(prop(result, "source"));
  return { dataset, artifacts };
}
