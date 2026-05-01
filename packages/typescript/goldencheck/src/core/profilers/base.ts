/**
 * Base profiler interface — all column profilers implement this.
 * Mirrors goldencheck/profilers/base.py.
 */

import type { TabularData } from "../data.js";
import type { Finding } from "../types.js";

/** Column profiler: runs on a single column of a TabularData. */
export interface Profiler {
  profile(
    data: TabularData,
    column: string,
    context?: Record<string, unknown>,
  ): Finding[];
}

/** Relation profiler: runs on the full dataset (cross-column). */
export interface RelationProfiler {
  profile(data: TabularData): Finding[];
}
