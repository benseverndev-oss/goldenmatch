/**
 * Zero-config front door: certify every declared key in a semantic model.
 *
 * `certifySemanticModel(doc, frames)` auto-detects the dialect (dbt/MetricFlow,
 * Cube, or OSI/Ossie), reads every declared identity key, and certifies each
 * against the supplied frames via the key-integrity certifier — "point it at the
 * semantic model you already have and get a fan-out report on the first run."
 *
 * Faithful port of Python `semantic/certify.py::certify_semantic_model`, the
 * STRUCTURAL tier only (uniqueness + fan-out). The Python `resolve=true`
 * fragmentation/undercount tier runs entity resolution on the record attributes
 * and is deliberately Python-only (see `keyIntegrity.ts`). Advisory only — it
 * never mutates a metric or a key.
 */

import { type LoadedDoc } from "./parseUtil.js";
import type { SemanticFrames } from "./frame.js";
import type { KeyIntegrityCertificate } from "./keyIntegrity.js";
import { certifyKeyIntegrity, resolveKeyIntegrity } from "./keyIntegrity.js";
import { parseSemanticModels } from "./metricflow.js";
import { certifyCubeJoins, certifyCubeJoinsResolved } from "./cube.js";
import { certifyOsiRelationships, certifyOsiRelationshipsResolved } from "./osi.js";
import type { SemanticDialect } from "./catalog.js";

/** One certified declared key: which target declared it, the key column(s), the
 * advisory certificate, and the join/relationship it feeds. */
export interface KeyCertification {
  target: string; // the model / dataset / cube name
  key: string[];
  certificate: KeyIntegrityCertificate;
  context: string; // e.g. the relationship / join it feeds
}

/**
 * Detect the semantic-layer dialect from a loaded document's top-level shape.
 * Cube uses a `cubes:` list, MetricFlow a `semantic_models:` list, OSI/Ossie a
 * `semantic_model` list (singular) + `version`. Throws if none match. Mirrors
 * Python `detect_dialect`.
 */
export function detectDialect(doc: LoadedDoc): SemanticDialect {
  if ("cubes" in doc) return "cube";
  if ("semantic_models" in doc) return "metricflow";
  if ("semantic_model" in doc) return "osi";
  throw new Error(
    "certifySemanticModel: could not detect a semantic-layer dialect; expected a " +
      "top-level 'cubes' (Cube), 'semantic_models' (dbt/MetricFlow), or " +
      "'semantic_model' (OSI/Ossie) key",
  );
}

/** The result of certifying a whole semantic model. */
export class SemanticCertification {
  readonly dialect: SemanticDialect;
  readonly entries: KeyCertification[];
  readonly skipped: string[]; // targets with no supplied frame
  readonly note: string;

  constructor(init: {
    dialect: SemanticDialect;
    entries?: KeyCertification[];
    skipped?: string[];
    note?: string;
  }) {
    this.dialect = init.dialect;
    this.entries = init.entries ?? [];
    this.skipped = init.skipped ?? [];
    this.note = init.note ?? "";
  }

  get nCertified(): number {
    return this.entries.length;
  }

  /** Entries whose declared key is NOT unique at grain (or fans out) — the keys a
   * metric silently depends on that will miscount. */
  get untrustworthy(): KeyCertification[] {
    return this.entries.filter((e) => !e.certificate.isTrustworthy());
  }

  get allTrustworthy(): boolean {
    return this.untrustworthy.length === 0;
  }
}

/**
 * Certify every declared identity key in a semantic model (structural tier).
 *
 * @param doc     an already-loaded semantic-model document (the dialect is
 *   auto-detected). The file read + YAML parse is a node concern (the MCP
 *   handler), keeping this core edge-safe — mirroring how Python's parsers accept
 *   a loaded dict.
 * @param frames  maps each model/dataset/cube name to the frame backing it
 *   (`{ column: values[] }`). A target with no supplied frame is skipped.
 */
export function certifySemanticModel(doc: LoadedDoc, frames: SemanticFrames): SemanticCertification {
  const dialect = detectDialect(doc);
  const entries: KeyCertification[] = [];
  const skipped: string[] = [];

  if (dialect === "metricflow") {
    for (const spec of parseSemanticModels(doc)) {
      const df = frames[spec.model];
      if (df === undefined) {
        skipped.push(spec.model);
        continue;
      }
      const cert = certifyKeyIntegrity(df, {
        key: spec.key,
        measures: spec.measures,
        ...(spec.grain !== null ? { grain: spec.grain } : {}),
      });
      entries.push({ target: spec.model, key: [...spec.key], certificate: cert, context: "" });
    }
  } else if (dialect === "cube") {
    for (const rep of certifyCubeJoins(doc, frames)) {
      entries.push({
        target: rep.toCube,
        key: [...rep.key],
        certificate: rep.certificate,
        context: `join from ${rep.fromCube}`,
      });
    }
  } else {
    for (const rep of certifyOsiRelationships(doc, frames)) {
      entries.push({
        target: rep.dataset,
        key: [...rep.key],
        certificate: rep.certificate,
        context: `relationship ${rep.relationship}`,
      });
    }
  }

  return new SemanticCertification({ dialect, entries, skipped });
}

/**
 * Async resolve-tier counterpart of {@link certifySemanticModel}: certifies every
 * declared identity key AND runs entity resolution on each frame's attributes to
 * measure fragmentation / undercount — the part a semantic layer can't do itself.
 * Mirrors Python `certify_semantic_model(..., resolve=True)`.
 *
 * Attribute selection is BLIND (all non-key, non-measure columns for MetricFlow;
 * all non-key columns for Cube/OSI, matching Python which passes no measures on
 * those paths). The metric-aware role-driven selection (`metric_aware=True`, which
 * resolves on the model's declared **dimensions** only) needs the
 * `semantic/blocking.py` roles reader and is a follow-up — a model that declares
 * no dimensions is byte-identical to blind selection, so this covers that case.
 * Each key's resolution is fail-open; a failure leaves the resolve fields null.
 */
export async function certifySemanticModelResolved(
  doc: LoadedDoc,
  frames: SemanticFrames,
): Promise<SemanticCertification> {
  const dialect = detectDialect(doc);
  const entries: KeyCertification[] = [];
  const skipped: string[] = [];

  if (dialect === "metricflow") {
    for (const spec of parseSemanticModels(doc)) {
      const df = frames[spec.model];
      if (df === undefined) {
        skipped.push(spec.model);
        continue;
      }
      const cert = await resolveKeyIntegrity(df, {
        key: spec.key,
        measures: spec.measures,
        ...(spec.grain !== null ? { grain: spec.grain } : {}),
      });
      entries.push({ target: spec.model, key: [...spec.key], certificate: cert, context: "" });
    }
  } else if (dialect === "cube") {
    for (const rep of await certifyCubeJoinsResolved(doc, frames)) {
      entries.push({
        target: rep.toCube,
        key: [...rep.key],
        certificate: rep.certificate,
        context: `join from ${rep.fromCube}`,
      });
    }
  } else {
    for (const rep of await certifyOsiRelationshipsResolved(doc, frames)) {
      entries.push({
        target: rep.dataset,
        key: [...rep.key],
        certificate: rep.certificate,
        context: `relationship ${rep.relationship}`,
      });
    }
  }

  return new SemanticCertification({ dialect, entries, skipped });
}
