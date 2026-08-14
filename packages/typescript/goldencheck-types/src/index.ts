export {
  loadDomain,
  listDomains,
  clearCache,
  DomainPackError,
} from "./loader.js";
export type {
  FieldSpec,
  FieldGroupSpec,
  DomainPack,
  FieldMapping,
  InferredSchema,
  DetectionResult,
  DetectionReason,
  RoleSpec,
  IdentityKind,
  IdentityLayer,
  LayerDetectionResult,
  LayerReason,
} from "./types.js";
export {
  isUnknown,
  unmappedCols,
  UNMAPPED_TYPE,
  SCHEMA_VERSION,
  isUnknownRole,
  IDENTITY_KINDS,
  LAYER_REASONS,
  UNKNOWN_ROLE,
} from "./types.js";
