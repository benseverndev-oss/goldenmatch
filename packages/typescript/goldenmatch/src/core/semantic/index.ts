/**
 * Semantic-layer interoperability (edge-safe subset).
 *
 * GoldenMatch produces the resolved, conformed entity keys a semantic layer's
 * joins and measures silently assume. The TS port ships the key-integrity
 * certifier, the Customer 360 serving-join certificate, and the dialect catalog
 * emitters (MetricFlow / Cube / OSI) — including `emitSemanticModelFromStore`,
 * which regenerates the conformed `resolved_entity_id` join declaration live from
 * the durable IdentityStore.
 */
export {
  KeyIntegrityCertificate,
  certifyKeyIntegrity,
  type KeyIntegrityCertificateInit,
} from "./keyIntegrity.js";
export { ServingJoinCertificate, certifyServingJoins } from "./serving.js";
export {
  ResolvedCrosswalk,
  pyRound6,
  type ResolvedCrosswalkInit,
} from "./crosswalk.js";
export { PyFloat, dumpYaml, pyFloat, type YamlValue } from "./yamlEmit.js";
export {
  emitSemanticModel,
  emitMetricflowYaml,
  emitFromCrosswalk,
  type EmitSemanticModelOptions,
} from "./metricflow.js";
export {
  emitCubeFromCrosswalk,
  type EmitCubeFromCrosswalkOptions,
  type CubeKeyIntegrityCertificateLike,
} from "./cube.js";
export {
  emitOsiFromCrosswalk,
  OSI_VERSION,
  DEFAULT_DIALECT,
  type EmitOsiFromCrosswalkOptions,
} from "./osi.js";
export {
  emitSemanticModelFromStore,
  type SemanticDialect,
  type EmitSemanticModelFromStoreOptions,
  type EmitDialectExtras,
} from "./catalog.js";
