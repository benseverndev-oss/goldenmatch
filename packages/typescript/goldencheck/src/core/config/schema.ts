/**
 * Runtime validation for GoldenCheckConfig — port of goldencheck/config/schema.py.
 * No Zod/Pydantic — plain validation functions.
 */

import {
  type GoldenCheckConfig,
  type ColumnRule,
  type Settings,
  type RelationRule,
  type IgnoreEntry,
  defaultConfig,
  defaultSettings,
} from "../types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function assertString(v: unknown, path: string): string {
  if (typeof v !== "string") {
    throw new Error(`${path}: expected string, got ${typeof v}`);
  }
  return v;
}

function assertNumber(v: unknown, path: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new Error(`${path}: expected finite number, got ${typeof v === "number" ? v : typeof v}`);
  }
  return v;
}

function assertBoolean(v: unknown, path: string): boolean {
  if (typeof v !== "boolean") {
    throw new Error(`${path}: expected boolean, got ${typeof v}`);
  }
  return v;
}

function assertArray(v: unknown, path: string): unknown[] {
  if (!Array.isArray(v)) {
    throw new Error(`${path}: expected array, got ${typeof v}`);
  }
  return v;
}

// ---------------------------------------------------------------------------
// Sub-validators
// ---------------------------------------------------------------------------

function validateSettings(raw: unknown, path: string): Settings {
  if (raw === undefined || raw === null) return defaultSettings();
  if (!isRecord(raw)) throw new Error(`${path}: expected object`);

  const defaults = defaultSettings();
  const sampleSize =
    raw.sample_size !== undefined
      ? assertNumber(raw.sample_size, `${path}.sample_size`)
      : raw.sampleSize !== undefined
        ? assertNumber(raw.sampleSize, `${path}.sampleSize`)
        : defaults.sampleSize;

  const severityThreshold =
    raw.severity_threshold !== undefined
      ? assertString(raw.severity_threshold, `${path}.severity_threshold`)
      : raw.severityThreshold !== undefined
        ? assertString(raw.severityThreshold, `${path}.severityThreshold`)
        : defaults.severityThreshold;

  const failOn =
    raw.fail_on !== undefined
      ? assertString(raw.fail_on, `${path}.fail_on`)
      : raw.failOn !== undefined
        ? assertString(raw.failOn, `${path}.failOn`)
        : defaults.failOn;

  return { sampleSize, severityThreshold, failOn };
}

function validateColumnRule(raw: unknown, path: string): ColumnRule {
  if (!isRecord(raw)) throw new Error(`${path}: expected object`);

  const type = assertString(raw.type, `${path}.type`);

  const rule: ColumnRule = {
    type,
    required: raw.required !== undefined ? assertBoolean(raw.required, `${path}.required`) : undefined,
    nullable: raw.nullable !== undefined ? assertBoolean(raw.nullable, `${path}.nullable`) : undefined,
    format: raw.format !== undefined ? assertString(raw.format, `${path}.format`) : undefined,
    unique: raw.unique !== undefined ? assertBoolean(raw.unique, `${path}.unique`) : undefined,
    range: raw.range !== undefined ? validateRange(raw.range, `${path}.range`) : undefined,
    enum: raw.enum !== undefined ? validateEnum(raw.enum, `${path}.enum`) : undefined,
    outlierStddev:
      raw.outlier_stddev !== undefined
        ? assertNumber(raw.outlier_stddev, `${path}.outlier_stddev`)
        : raw.outlierStddev !== undefined
          ? assertNumber(raw.outlierStddev, `${path}.outlierStddev`)
          : undefined,
  };

  return rule;
}

function validateRange(raw: unknown, path: string): readonly [number, number] {
  const arr = assertArray(raw, path);
  if (arr.length !== 2) throw new Error(`${path}: range must have exactly 2 elements`);
  const lo = assertNumber(arr[0], `${path}[0]`);
  const hi = assertNumber(arr[1], `${path}[1]`);
  return [lo, hi] as const;
}

function validateEnum(raw: unknown, path: string): readonly string[] {
  const arr = assertArray(raw, path);
  return arr.map((v, i) => assertString(v, `${path}[${i}]`));
}

function validateColumns(
  raw: unknown,
  path: string,
): Readonly<Record<string, ColumnRule>> {
  if (raw === undefined || raw === null) return {};
  if (!isRecord(raw)) throw new Error(`${path}: expected object`);

  const result: Record<string, ColumnRule> = {};
  for (const [key, value] of Object.entries(raw)) {
    result[key] = validateColumnRule(value, `${path}.${key}`);
  }
  return result;
}

function validateRelationRule(raw: unknown, path: string): RelationRule {
  if (!isRecord(raw)) throw new Error(`${path}: expected object`);
  const type = assertString(raw.type, `${path}.type`);
  const columns = assertArray(raw.columns, `${path}.columns`).map((v, i) =>
    assertString(v, `${path}.columns[${i}]`),
  );
  return { type, columns };
}

function validateRelations(raw: unknown, path: string): readonly RelationRule[] {
  if (raw === undefined || raw === null) return [];
  const arr = assertArray(raw, path);
  return arr.map((v, i) => validateRelationRule(v, `${path}[${i}]`));
}

function validateIgnoreEntry(raw: unknown, path: string): IgnoreEntry {
  if (!isRecord(raw)) throw new Error(`${path}: expected object`);
  const column = assertString(raw.column, `${path}.column`);
  const check = assertString(raw.check, `${path}.check`);
  return { column, check };
}

function validateIgnore(raw: unknown, path: string): readonly IgnoreEntry[] {
  if (raw === undefined || raw === null) return [];
  const arr = assertArray(raw, path);
  return arr.map((v, i) => validateIgnoreEntry(v, `${path}[${i}]`));
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Validate a raw parsed object (e.g. from YAML/JSON) into a GoldenCheckConfig.
 * Throws on invalid input. Returns a fully populated config with defaults.
 */
export function validateConfig(raw: unknown): GoldenCheckConfig {
  if (raw === undefined || raw === null) return defaultConfig();
  if (!isRecord(raw)) throw new Error("config: expected object at root level");

  const version =
    raw.version !== undefined ? assertNumber(raw.version, "config.version") : 1;

  const settings = validateSettings(raw.settings, "config.settings");
  const columns = validateColumns(raw.columns, "config.columns");
  const relations = validateRelations(raw.relations, "config.relations");
  const ignore = validateIgnore(raw.ignore, "config.ignore");

  return { version, settings, columns, relations, ignore };
}
