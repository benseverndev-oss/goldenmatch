/**
 * Semantic-layer interoperability (edge-safe subset).
 *
 * GoldenMatch produces the resolved, conformed entity keys a semantic layer's
 * joins and measures silently assume. The TS port ships the key-integrity
 * certifier, the Customer 360 serving-join certificate, the dialect catalog
 * emitters (MetricFlow / Cube / OSI, incl. `emitSemanticModelFromStore`), AND the
 * consume side — `certifySemanticModel` parses a dbt/Cube/OSI model and certifies
 * every key its metrics join on. Produce and consume are now both cross-surface.
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
  parseSemanticModels,
  type EmitSemanticModelOptions,
  type DeclaredKeySpec,
} from "./metricflow.js";
export {
  emitCubeFromCrosswalk,
  parseCubeModels,
  emitCubeYaml,
  cubeJoinKeys,
  certifyCubeJoins,
  type EmitCubeFromCrosswalkOptions,
  type CubeKeyIntegrityCertificateLike,
  type ParsedCube,
  type ParsedCubeJoin,
  type CubeDimension,
  type CubeMeasure,
  type CubeJoinKey,
  type CertifiedJoin,
} from "./cube.js";
export {
  emitOsiFromCrosswalk,
  parseOsiModels,
  emitOsiYaml,
  osiJoinKeys,
  certifyOsiRelationships,
  OSI_VERSION,
  DEFAULT_DIALECT,
  type EmitOsiFromCrosswalkOptions,
  type ParsedOsiModel,
  type ParsedOsiRelationship,
  type OsiField,
  type OsiDataset,
  type OsiMetric,
  type OsiJoinKey,
  type CertifiedRelationship,
} from "./osi.js";
export {
  emitSemanticModelFromStore,
  type SemanticDialect,
  type EmitSemanticModelFromStoreOptions,
  type EmitDialectExtras,
} from "./catalog.js";
export {
  certifySemanticModel,
  detectDialect,
  SemanticCertification,
  type KeyCertification,
} from "./certify.js";
export type { SemanticFrame, SemanticFrames } from "./frame.js";
