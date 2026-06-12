/**
 * `match` adapter — a GoldenMatch `DedupeResult`-like object → `AnalyzerInput`.
 *
 * Duck-typed: reads `.clusters` / `.scoredPairs|.scored_pairs` / `.stats` / `.config`
 * off the result, so it imports nothing from goldenmatch. The recall certificate is
 * optional — passed in by the caller, or read off the result when the producer
 * attached one. Parity with `adapters/match.py`.
 */

import type { AnalyzerInput } from "../types.js";

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function prop(obj: unknown, key: string): unknown {
  return obj !== null && typeof obj === "object" ? (obj as Record<string, unknown>)[key] : undefined;
}

/** Normalize a recall certificate to `{estimate, safe_bound}` (or null). */
export function normalizeCert(cert: unknown): { estimate: number | null; safe_bound: number | null } | null {
  if (cert === null || cert === undefined || typeof cert !== "object") return null;
  const c = cert as Record<string, unknown>;
  return {
    estimate: asNumber(c["estimate"] ?? c["recall"]),
    safe_bound: asNumber(c["safe_bound"] ?? c["recall_lower"]),
  };
}

/** Best-effort: the first matchkey's threshold from the result's config. */
function primaryThreshold(config: unknown): number | null {
  try {
    const getter = prop(config, "getMatchkeys") ?? prop(config, "get_matchkeys");
    const matchkeys =
      typeof getter === "function"
        ? (getter as () => unknown[]).call(config)
        : (prop(config, "matchkeys") as unknown[] | undefined);
    for (const mk of matchkeys ?? []) {
      const thr = prop(mk, "threshold");
      if (thr !== null && thr !== undefined) return Number(thr);
    }
  } catch {
    return null;
  }
  return null;
}

export interface MatchAdapterOptions {
  readonly dataset?: string;
  readonly certificate?: unknown;
}

export function matchArtifacts(result: unknown, options: MatchAdapterOptions = {}): AnalyzerInput {
  const cert =
    options.certificate !== undefined && options.certificate !== null
      ? options.certificate
      : prop(result, "recallCertificate") ?? prop(result, "recall_certificate");
  const artifacts: Record<string, unknown> = {
    __producer__: "goldenmatch",
    clusters: prop(result, "clusters") ?? {},
    scored_pairs: prop(result, "scoredPairs") ?? prop(result, "scored_pairs") ?? [],
    match_stats: prop(result, "stats") ?? {},
    match_threshold: primaryThreshold(prop(result, "config")),
  };
  const normalized = normalizeCert(cert);
  if (normalized !== null) artifacts["recall_certificate"] = normalized;
  return { dataset: options.dataset ?? "match", artifacts };
}
