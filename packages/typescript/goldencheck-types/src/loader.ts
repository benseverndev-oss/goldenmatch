// Load domain packs from yaml files at runtime.
import * as fs from "fs";
import * as path from "path";
import * as url from "url";
import * as yaml from "js-yaml";
import type { DomainPack, FieldSpec } from "./types.js";

function domainsDir(): string {
  if (process.env.GOLDENCHECK_TYPES_TEST_DIR) {
    return process.env.GOLDENCHECK_TYPES_TEST_DIR;
  }
  // Resolve relative to this file. In dev (src/) we go up one to find domains/;
  // in dist/ we also go up one (dist/loader.js -> ../domains).
  const here = path.dirname(url.fileURLToPath(import.meta.url));
  return path.resolve(here, "..", "domains");
}

export function listDomains(): string[] {
  return fs
    .readdirSync(domainsDir())
    .filter((f) => f.endsWith(".yaml"))
    .map((f) => f.replace(/\.yaml$/, ""))
    .sort();
}

export function loadDomain(name: string): DomainPack {
  const filePath = path.join(domainsDir(), `${name}.yaml`);
  if (!fs.existsSync(filePath)) {
    throw new Error(
      `domain pack '${name}' not found in ${domainsDir()}`,
    );
  }
  const raw = (yaml.load(fs.readFileSync(filePath, "utf-8")) as
    | Record<string, any>
    | null) ?? {};
  const types: Record<string, FieldSpec> = {};
  const rawTypes = (raw.types ?? {}) as Record<string, any>;
  for (const [typeName, spec] of Object.entries(rawTypes)) {
    const threshold = spec.confidence_threshold;
    if (threshold !== undefined && (threshold < 0 || threshold > 1)) {
      throw new Error(
        `confidence_threshold for ${name}.${typeName} must be in [0,1], got ${threshold}`,
      );
    }
    types[typeName] = {
      name_hints: spec.name_hints ?? [],
      value_signals: spec.value_signals ?? {},
      suppress: spec.suppress ?? [],
      confidence_threshold: threshold,
      description: spec.description,
    };
  }
  return {
    name,
    description: raw.description ?? "",
    types,
  };
}
