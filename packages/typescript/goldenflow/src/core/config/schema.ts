/**
 * Config schema — re-exports types and provides runtime validation.
 */

import type { GoldenFlowConfig } from "../types.js";
import { makeConfig } from "../types.js";

export { makeConfig } from "../types.js";
export type {
  GoldenFlowConfig,
  TransformSpec,
  SplitSpec,
  FilterSpec,
  DedupSpec,
  MappingSpec,
} from "../types.js";

/** Basic runtime validation (not as strict as Pydantic, but catches common errors). */
export function validateConfig(raw: Record<string, unknown>): GoldenFlowConfig {
  const transforms = Array.isArray(raw["transforms"])
    ? raw["transforms"].map((t: Record<string, unknown>) => ({
        column: String(t["column"] ?? ""),
        ops: Array.isArray(t["ops"]) ? t["ops"].map(String) : [],
      }))
    : [];

  const splits = Array.isArray(raw["splits"])
    ? raw["splits"].map((s: Record<string, unknown>) => ({
        source: String(s["source"] ?? ""),
        target: Array.isArray(s["target"]) ? s["target"].map(String) : [],
        method: String(s["method"] ?? ""),
      }))
    : [];

  const renames: Record<string, string> =
    raw["renames"] && typeof raw["renames"] === "object"
      ? Object.fromEntries(
          Object.entries(raw["renames"] as Record<string, unknown>).map(
            ([k, v]) => [k, String(v)],
          ),
        )
      : {};

  const drop = Array.isArray(raw["drop"]) ? raw["drop"].map(String) : [];

  const filters = Array.isArray(raw["filters"])
    ? raw["filters"].map((f: Record<string, unknown>) => ({
        column: String(f["column"] ?? ""),
        condition: String(f["condition"] ?? ""),
      }))
    : [];

  const dedupRaw = raw["dedup"] as Record<string, unknown> | null | undefined;
  const dedup =
    dedupRaw && typeof dedupRaw === "object"
      ? {
          columns: Array.isArray(dedupRaw["columns"])
            ? dedupRaw["columns"].map(String)
            : [],
          keep: (dedupRaw["keep"] === "last" ? "last" : "first") as "first" | "last",
        }
      : null;

  const mappings = Array.isArray(raw["mappings"])
    ? raw["mappings"].map((m: Record<string, unknown>) => ({
        source: String(m["source"] ?? ""),
        target: m["target"] as string | readonly string[],
        transform: (m["transform"] as string | readonly string[] | null) ?? null,
      }))
    : [];

  return makeConfig({
    source: raw["source"] != null ? String(raw["source"]) : null,
    output: raw["output"] != null ? String(raw["output"]) : null,
    transforms,
    splits,
    renames,
    drop,
    filters,
    dedup,
    mappings,
  });
}
