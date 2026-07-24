/**
 * domain-registry.ts -- discover / load / save domain rulebooks (node half).
 *
 * Port of the filesystem side of Python `core/domain_registry.py`. The pure
 * logic (shape, compile, extract, match) is edge-safe in
 * `src/core/domain-rulebook.ts`; this module owns YAML + `node:fs` so the core
 * stays `node:*`-free.
 *
 * Search order mirrors Python -- later paths do NOT override earlier ones by
 * position; the LAST loaded name wins, exactly as Python's dict assignment does:
 *   1. `.goldenmatch/domains/`            (project-local)
 *   2. `~/.goldenmatch/domains/`          (user global)
 *
 * DELIBERATE DIFFERENCE: Python has a third, built-in `goldenmatch/domains/`
 * directory shipped inside the wheel (7 packs: electronics, software, ...).
 * The TS package ships no YAML packs -- its built-in domain knowledge is the
 * compiled-in `core/domain.ts` extractors -- so there is no third search path.
 * `list_domains` on TS therefore returns only user-authored rulebooks, and is
 * empty on a fresh install where Python's would list the 7 built-ins.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from "node:fs";
import { join, resolve, extname, basename } from "node:path";
import { homedir } from "node:os";
import { createRequire } from "node:module";
import type { DomainRulebook } from "../core/domain-rulebook.js";
import { makeRulebook } from "../core/domain-rulebook.js";

/** Rulebook search directories, highest precedence LAST (last loaded wins). */
export function searchPaths(): string[] {
  return [
    resolve(".goldenmatch", "domains"),
    join(homedir(), ".goldenmatch", "domains"),
  ];
}

interface YamlModule {
  parse(src: string): unknown;
  stringify(value: unknown, opts?: Record<string, unknown>): string;
}

/**
 * `yaml` is an OPTIONAL peer dep (same posture as better-sqlite3). Resolve it
 * lazily and fail with an actionable message instead of a bare MODULE_NOT_FOUND.
 */
function loadYaml(): YamlModule {
  try {
    const req = createRequire(import.meta.url);
    return req("yaml") as YamlModule;
  } catch {
    throw new Error(
      "Domain rulebooks are YAML; install the optional peer dependency: npm install yaml",
    );
  }
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function asStringRecord(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val === "string") out[k] = val;
    }
  }
  return out;
}

/** Load one rulebook from a YAML file. Throws if the file is missing. */
export function loadRulebook(path: string): DomainRulebook {
  if (!existsSync(path)) throw new Error(`Domain rulebook not found: ${path}`);
  const data = loadYaml().parse(readFileSync(path, "utf-8"));
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const fallbackName = basename(path, extname(path));
  return makeRulebook(
    typeof obj["name"] === "string" ? obj["name"] : fallbackName,
    {
      signals: asStringArray(obj["signals"]),
      identifierPatterns: asStringRecord(obj["identifier_patterns"]),
      brandPatterns: asStringArray(obj["brand_patterns"]),
      attributePatterns: asStringRecord(obj["attribute_patterns"]),
      stopWords: asStringArray(obj["stop_words"]),
      normalization: asStringRecord(obj["normalization"]),
    },
  );
}

/**
 * Write a rulebook to YAML, creating parent dirs. Keys are emitted in Python's
 * order (`sort_keys=False`) and in Python's snake_case so a rulebook authored by
 * either toolkit loads in the other.
 */
export function saveRulebook(rb: DomainRulebook, path: string): string {
  const yaml = loadYaml();
  const dir = resolve(path, "..");
  mkdirSync(dir, { recursive: true });
  const data = {
    name: rb.name,
    signals: [...rb.signals],
    identifier_patterns: { ...rb.identifierPatterns },
    brand_patterns: [...rb.brandPatterns],
    attribute_patterns: { ...rb.attributePatterns },
    stop_words: [...rb.stopWords],
    normalization: { ...rb.normalization },
  };
  writeFileSync(path, yaml.stringify(data), "utf-8");
  return resolve(path);
}

/**
 * Discover every rulebook across the search paths, keyed by rulebook name.
 * A file that fails to parse is SKIPPED (Python logs a warning) so one broken
 * YAML can't hide every other domain.
 */
export function discoverRulebooks(): Map<string, DomainRulebook> {
  const found = new Map<string, DomainRulebook>();
  for (const dir of searchPaths()) {
    if (!existsSync(dir)) continue;
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }
    for (const entry of entries.sort()) {
      const ext = extname(entry).toLowerCase();
      if (ext !== ".yaml" && ext !== ".yml") continue;
      try {
        const rb = loadRulebook(join(dir, entry));
        found.set(rb.name, rb);
      } catch {
        // skip unreadable / invalid rulebook, like Python's warn-and-continue
      }
    }
  }
  return found;
}
