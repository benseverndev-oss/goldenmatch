/**
 * runtimeProfile.ts — machine-level signals consumed by the controller-v3
 * planner. Edge-safe: no `node:` imports (the capture helper that reads real
 * RAM/CPU lives in src/node/, since psutil-equivalents are Node-only). The
 * pure data shape is here so the planner rules can be unit/parity tested.
 *
 * Ports goldenmatch/core/runtime_profile.py.
 */

export interface RuntimeProfile {
  /** Available RAM in gigabytes. */
  readonly availableRamGb: number;
  /** CPU core count (fall back to 1). */
  readonly cpuCount: number;
  /** Free disk in gigabytes (spill-threshold sanity checks). */
  readonly diskFreeGb: number;
}

export function makeRuntimeProfile(p: Partial<RuntimeProfile> = {}): RuntimeProfile {
  return {
    availableRamGb: p.availableRamGb ?? 8.0,
    cpuCount: p.cpuCount ?? 1,
    diskFreeGb: p.diskFreeGb ?? 100.0,
  };
}
