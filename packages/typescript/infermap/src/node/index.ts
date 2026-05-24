// Node-only surface: DB providers + filesystem file reader.
// Edge runtimes must NOT import this module — it pulls in `node:fs`.
export { extractSchemaFromFile } from "./fs.js";
export {
  extractDbSchema,
  type DbExtractOptions,
} from "./db/provider.js";
export {
  parseConnection,
  sqliteTypeToInfermap,
  pgTypeToInfermap,
  duckdbTypeToInfermap,
  DbError,
} from "./db/types.js";
export type {
  DbDriver,
  ConnInfo,
  SqliteConnInfo,
  PgConnInfo,
  DuckdbConnInfo,
  MysqlConnInfo,
} from "./db/types.js";

// InferMap -> Identity Graph bridge (depends on the goldenmatch IdentityStore).
export {
  writeAliasesFromMapping,
  aliasWriteResultAsDict,
  DEFAULT_ALIAS_KINDS,
  DEFAULT_MIN_CONFIDENCE,
} from "./identity.js";
export type {
  AliasWriteResult,
  EntityIdResolver,
  WriteAliasesOptions,
} from "./identity.js";
