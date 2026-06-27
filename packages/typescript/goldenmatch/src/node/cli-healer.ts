/**
 * cli-healer.ts -- the CLI healer surface emitter (mirror of Python
 * cli/dedupe.py::_emit_healer_surface).
 *
 * Pure printing over an ALREADY-PRODUCED DedupeResult:
 *   --heal     -> print the applied trail + a "ran with the healed config" note.
 *   --suggest  -> print the serialized suggestions (kind -> rationale lines).
 *   (default)  -> print a one-line headroom hint to STDERR, ONLY when the free
 *                 trigger fires. The trigger is read off `result.postflightReport`
 *                 via `headroomSignal` -- NEVER a second dedupe run (the cost
 *                 guarantee: the surface reuses the result `dedupe()` already
 *                 produced).
 *
 * Writers are injected so the surface is unit-testable without driving the
 * commander tree (matches the repo's CLI-test convention).
 */

import type { DedupeResult } from "../core/types.js";
import { headroomSignal } from "../core/suggest.js";

export interface HealerSurfaceOptions {
  readonly suggest?: boolean;
  readonly heal?: boolean;
}

export interface HealerWriters {
  readonly out: (s: string) => void;
  readonly err: (s: string) => void;
}

/**
 * Emit the healer surface for a completed dedupe run. See the module doc for
 * the per-mode behavior. Never re-runs the pipeline -- everything is read off
 * the passed `result`.
 */
export function emitHealerSurface(
  result: DedupeResult,
  opts: HealerSurfaceOptions,
  writers: HealerWriters,
): void {
  if (opts.heal === true) {
    const trail = result.healTrail ?? [];
    if (trail.length === 0) {
      writers.out("\nHealer: no changes applied (config already healthy).\n");
      return;
    }
    writers.out(`\nHealer applied ${trail.length} change(s):\n`);
    for (const s of trail) {
      writers.out(`  [${s.kind}] ${s.rationale}\n`);
    }
    writers.out("Re-ran with the healed config; the results above reflect it.\n");
    return;
  }

  if (opts.suggest === true) {
    const suggestions = result.suggestions ?? [];
    if (suggestions.length === 0) {
      writers.out("\nNo config suggestions -- the config looks healthy.\n");
      return;
    }
    writers.out(`\nConfig suggestions (${suggestions.length}):\n`);
    for (const s of suggestions) {
      writers.out(`  [${s.kind}] ${s.rationale}\n`);
    }
    return;
  }

  // Default run: free one-line hint to STDERR, only when the trigger fires.
  // Read off the already-produced postflight report -- no second dedupe.
  const reason = headroomSignal(result.postflightReport);
  if (reason !== null) {
    writers.err(
      `Hint: config may have headroom (${reason.detail}). ` +
        "Re-run with --suggest for config suggestions, or --heal to auto-apply.\n",
    );
  }
}
