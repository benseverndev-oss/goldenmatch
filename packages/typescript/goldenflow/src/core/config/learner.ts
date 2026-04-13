/**
 * Config learner — generate a config from data profiles.
 */

import type { GoldenFlowConfig, Row, TransformSpec } from "../types.js";
import { makeConfig } from "../types.js";
import { profileDataframe } from "../engine/profiler-bridge.js";
import { selectTransforms } from "../engine/selector.js";

export function learnConfig(rows: readonly Row[], source = ""): GoldenFlowConfig {
  const profile = profileDataframe(rows, source);
  const transforms: TransformSpec[] = [];

  for (const colProfile of profile.columns) {
    const selected = selectTransforms(colProfile);
    if (selected.length > 0) {
      transforms.push({
        column: colProfile.name,
        ops: selected.map((t) => t.name),
      });
    }
  }

  return makeConfig({
    source: source || null,
    transforms,
  });
}
