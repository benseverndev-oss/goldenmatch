/**
 * `check` adapter — GoldenCheck scan output → `AnalyzerInput.artifacts`.
 *
 * Ships only the pure `checkArtifacts` (the Python `from_scan` seam) — no goldencheck
 * import. The `load(df)` variant that lazy-imports goldencheck and runs
 * `scan_dataframe` is deferred (TS has no goldencheck dep yet). Parity with the
 * `from_scan` path of `adapters/check.py`.
 */

import type { AnalyzerInput } from "../types.js";

/** Normalize already-computed scan output (findings + optional profile). */
export function checkArtifacts(
  findings: unknown,
  profile: unknown = null,
  options: { dataset?: string } = {},
): AnalyzerInput {
  return {
    dataset: options.dataset ?? "check",
    artifacts: { __producer__: "goldencheck", findings, profile },
  };
}
