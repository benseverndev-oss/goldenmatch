/**
 * JSON reporter — serialize manifest to JSON.
 */

import type { Manifest } from "../types.js";
import { MutableManifest } from "../types.js";

export function manifestToJson(manifest: Manifest): string {
  if (manifest instanceof MutableManifest) {
    return JSON.stringify(manifest.toDict(), null, 2);
  }
  return JSON.stringify(manifest, null, 2);
}
