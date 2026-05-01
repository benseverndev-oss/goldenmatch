// Manifest loader + validator. Mirrors infermap_bench/manifest.py.
import { readFileSync } from "node:fs";
import type { CaseRef, Category, Difficulty } from "./types.js";
import { MANIFEST_VERSION } from "./index.js";

export class InvalidManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidManifestError";
  }
}
export class IncompatibleManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IncompatibleManifestError";
  }
}

const VALID_CATEGORIES = new Set<Category>(["valentine", "real_world", "synthetic"]);
const VALID_DIFFICULTIES = new Set<Difficulty>(["easy", "medium", "hard"]);
const REQUIRED_CASE_FIELDS = [
  "id", "path", "category", "subcategory", "source",
  "tags", "expected_difficulty", "field_counts",
] as const;

export function loadManifest(path: string): CaseRef[] {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      throw new Error(`Manifest not found: ${path}`);
    }
    throw e;
  }

  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    throw new InvalidManifestError(`Manifest is not valid JSON: ${String(e)}`);
  }

  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new InvalidManifestError("Manifest root must be a JSON object");
  }
  const obj = raw as Record<string, unknown>;
  if (typeof obj["version"] !== "number" || typeof obj["version"] === "boolean") {
    throw new InvalidManifestError("Manifest is missing or has non-integer 'version'");
  }
  if (obj["version"] > MANIFEST_VERSION) {
    throw new IncompatibleManifestError(
      `Manifest version ${obj["version"]} exceeds runner max ${MANIFEST_VERSION}. Upgrade @infermap/bench.`
    );
  }
  if (!Array.isArray(obj["cases"])) {
    throw new InvalidManifestError("Manifest 'cases' must be an array");
  }

  return (obj["cases"] as unknown[]).map((entry, idx) => validateEntry(entry, idx));
}

function validateEntry(entry: unknown, idx: number): CaseRef {
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
    throw new InvalidManifestError(`cases[${idx}] must be a JSON object`);
  }
  const e = entry as Record<string, unknown>;
  for (const key of REQUIRED_CASE_FIELDS) {
    if (!(key in e)) {
      throw new InvalidManifestError(`cases[${idx}] missing required field: ${key}`);
    }
  }
  const category = e["category"];
  if (typeof category !== "string" || !VALID_CATEGORIES.has(category as Category)) {
    throw new InvalidManifestError(`cases[${idx}].category invalid: ${String(category)}`);
  }
  const difficulty = e["expected_difficulty"];
  if (typeof difficulty !== "string" || !VALID_DIFFICULTIES.has(difficulty as Difficulty)) {
    throw new InvalidManifestError(`cases[${idx}].expected_difficulty invalid: ${String(difficulty)}`);
  }
  const src = e["source"];
  if (typeof src !== "object" || src === null || Array.isArray(src)) {
    throw new InvalidManifestError(`cases[${idx}].source must be an object`);
  }
  const srcObj = src as Record<string, unknown>;
  for (const k of ["name", "url", "license", "attribution"]) {
    if (typeof srcObj[k] !== "string") {
      throw new InvalidManifestError(`cases[${idx}].source.${k} must be a string`);
    }
  }
  const tags = e["tags"];
  if (!Array.isArray(tags) || !tags.every((t) => typeof t === "string")) {
    throw new InvalidManifestError(`cases[${idx}].tags must be string[]`);
  }
  const fc = e["field_counts"];
  if (typeof fc !== "object" || fc === null || Array.isArray(fc)) {
    throw new InvalidManifestError(`cases[${idx}].field_counts must be an object`);
  }
  const fcObj = fc as Record<string, unknown>;
  for (const k of ["source", "target"]) {
    const v = fcObj[k];
    if (typeof v !== "number" || typeof v === "boolean") {
      throw new InvalidManifestError(`cases[${idx}].field_counts.${k} must be a number`);
    }
  }
  if (Object.keys(fcObj).length !== 2) {
    throw new InvalidManifestError(`cases[${idx}].field_counts must have exactly source and target keys`);
  }

  return {
    id: e["id"] as string,
    path: e["path"] as string,
    category: category as Category,
    subcategory: e["subcategory"] as string,
    source: {
      name: srcObj["name"] as string,
      url: srcObj["url"] as string,
      license: srcObj["license"] as string,
      attribution: srcObj["attribution"] as string,
    },
    tags: tags as string[],
    expectedDifficulty: difficulty as Difficulty,
    fieldCounts: { source: fcObj["source"] as number, target: fcObj["target"] as number },
  };
}
