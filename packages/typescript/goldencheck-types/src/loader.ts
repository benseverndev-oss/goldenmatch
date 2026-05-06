// Load domain packs from yaml files at runtime.
import * as fs from "fs";
import * as path from "path";
import * as url from "url";
import * as yaml from "js-yaml";
import type { DomainPack, FieldSpec } from "./types.js";

/** A domain-pack YAML file is malformed (wrong shape, type, or value).
 *  Distinct from "file not found" so callers can react differently — a
 *  malformed pack is a fix-the-yaml situation, not a fix-the-call situation. */
export class DomainPackError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DomainPackError";
  }
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function domainsDir(): string {
  if (process.env.GOLDENCHECK_TYPES_TEST_DIR) {
    return process.env.GOLDENCHECK_TYPES_TEST_DIR;
  }
  // Resolve relative to this file. In dev (src/) we go up one to find domains/;
  // in dist/ we also go up one (dist/loader.js -> ../domains).
  const here = path.dirname(url.fileURLToPath(import.meta.url));
  return path.resolve(here, "..", "domains");
}

// Module-scoped pack cache. Keyed on (resolved-domains-dir, pack-name) so
// flipping GOLDENCHECK_TYPES_TEST_DIR between calls invalidates naturally
// without callers remembering to clearCache().
const _packCache = new Map<string, DomainPack>();

/** Drop memoized domain packs.
 *
 *  Tests that mutate YAML files on disk after the first `loadDomain` call
 *  must invoke this — otherwise subsequent loads return the cached
 *  pre-mutation pack. Production code never needs this. */
export function clearCache(): void {
  _packCache.clear();
}

export function listDomains(): string[] {
  return fs
    .readdirSync(domainsDir())
    .filter((f) => f.endsWith(".yaml"))
    .map((f) => f.replace(/\.yaml$/, ""))
    .sort();
}

/** Load and shape-validate a domain pack YAML (memoized; see `clearCache`).
 *
 * Mismatched types raise `DomainPackError` with the file path + key path
 * so the user can fix the YAML directly. Previously a misindented
 * `name_hints:` or a string-where-list-expected produced a pack that
 * "loaded fine" but matched nothing at runtime.
 */
export function loadDomain(name: string): DomainPack {
  const dir = domainsDir();
  const cacheKey = `${dir}::${name}`;
  const cached = _packCache.get(cacheKey);
  if (cached) return cached;

  const filePath = path.join(dir, `${name}.yaml`);
  if (!fs.existsSync(filePath)) {
    throw new Error(
      `domain pack '${name}' not found in ${dir}`,
    );
  }
  const parsed = yaml.load(fs.readFileSync(filePath, "utf-8")) as unknown;
  if (parsed === null || parsed === undefined) {
    throw new DomainPackError(
      `${filePath}: empty or null YAML; expected a mapping`,
    );
  }
  if (!isPlainObject(parsed)) {
    throw new DomainPackError(
      `${filePath}: top level must be a mapping, got ${typeof parsed}`,
    );
  }
  const raw = parsed;
  const rawTypesAny = raw.types;
  let rawTypes: Record<string, unknown>;
  if (rawTypesAny === undefined || rawTypesAny === null) {
    rawTypes = {};
  } else if (!isPlainObject(rawTypesAny)) {
    throw new DomainPackError(
      `${filePath}: 'types' must be a mapping, got ${typeof rawTypesAny}`,
    );
  } else {
    rawTypes = rawTypesAny;
  }

  const types: Record<string, FieldSpec> = {};
  for (const [typeName, specAny] of Object.entries(rawTypes)) {
    if (!isPlainObject(specAny)) {
      throw new DomainPackError(
        `${filePath}: types.${typeName} must be a mapping, got ${typeof specAny}`,
      );
    }
    const spec = specAny;

    const nameHints = spec.name_hints ?? [];
    if (!Array.isArray(nameHints)) {
      throw new DomainPackError(
        `${filePath}: types.${typeName}.name_hints must be a list, got ${typeof nameHints}`,
      );
    }
    const valueSignals = spec.value_signals ?? {};
    if (!isPlainObject(valueSignals)) {
      throw new DomainPackError(
        `${filePath}: types.${typeName}.value_signals must be a mapping, got ${typeof valueSignals}`,
      );
    }
    const suppress = spec.suppress ?? [];
    if (!Array.isArray(suppress)) {
      throw new DomainPackError(
        `${filePath}: types.${typeName}.suppress must be a list, got ${typeof suppress}`,
      );
    }
    const threshold = spec.confidence_threshold as number | undefined;
    if (threshold !== undefined) {
      if (typeof threshold !== "number" || Number.isNaN(threshold)) {
        throw new DomainPackError(
          `${filePath}: types.${typeName}.confidence_threshold must be numeric, got ${threshold}`,
        );
      }
      if (threshold < 0 || threshold > 1) {
        throw new DomainPackError(
          `${filePath}: types.${typeName}.confidence_threshold must be in [0,1], got ${threshold}`,
        );
      }
    }

    // If the YAML explicitly carries a `name:` it must match the dict
    // key. Disagreement signals user error (typo, copy-paste).
    if (
      spec.name !== undefined &&
      spec.name !== null &&
      spec.name !== typeName
    ) {
      throw new DomainPackError(
        `${filePath}: types.${typeName}.name is ${JSON.stringify(spec.name)}, ` +
          `but it lives under key ${JSON.stringify(typeName)}. The two must agree.`,
      );
    }
    types[typeName] = {
      name: typeName,
      name_hints: nameHints.map(String),
      value_signals: valueSignals,
      suppress: suppress.map(String),
      ...(threshold !== undefined ? { confidence_threshold: threshold } : {}),
      ...(typeof spec.description === "string" ? { description: spec.description } : {}),
    };
  }
  const pack: DomainPack = {
    name,
    description: typeof raw.description === "string" ? raw.description : "",
    types,
  };
  _packCache.set(cacheKey, pack);
  return pack;
}
