/**
 * domain-rulebook.ts -- user-defined domain extraction rulebooks (edge-safe half).
 *
 * Port of the pure logic in Python `core/domain_registry.py`: the rulebook
 * shape, regex compilation, `extract`, and `match_domain`. Everything here is
 * dependency-free and `node:*`-free so it runs on the edge; the filesystem half
 * (load / save / discover YAML) lives in `src/node/domain-registry.ts`.
 *
 * A rulebook is the user-authored counterpart to the built-in `core/domain.ts`
 * extractors: instead of hard-coded electronics/software heuristics, the user
 * declares signals + regex patterns for their own domain (medical devices,
 * automotive parts, ...).
 *
 * REGEX PARITY CAVEAT: Python `re` and JS `RegExp` are not the same engine.
 * Patterns using only the common subset (literals, classes, groups, `\b`,
 * `\d`, `\s`, quantifiers) behave identically; Python-specific constructs
 * (named groups `(?P<x>...)`, possessive/atomic groups, conditionals) do NOT
 * translate and are reported as invalid rather than silently mis-matching.
 * `\w` is normalized below so word-splitting stays Unicode-aware like Python's.
 */

export interface DomainRulebook {
  readonly name: string;
  readonly signals: readonly string[];
  /** name -> regex source, e.g. `{ ndc: "\\b(\\d{5}-\\d{4}-\\d{2})\\b" }` */
  readonly identifierPatterns: Readonly<Record<string, string>>;
  /** literal brand strings (escaped + alternated at compile time) */
  readonly brandPatterns: readonly string[];
  readonly attributePatterns: Readonly<Record<string, string>>;
  readonly stopWords: readonly string[];
  readonly normalization: Readonly<Record<string, string>>;
}

export interface CompiledRulebook {
  readonly rulebook: DomainRulebook;
  readonly identifiers: ReadonlyMap<string, RegExp>;
  readonly attributes: ReadonlyMap<string, RegExp>;
  readonly brands: RegExp | null;
  /** Patterns that failed to compile (Python logs a warning and skips them). */
  readonly invalid: readonly string[];
}

export interface RulebookExtraction {
  readonly brand: string | null;
  readonly identifiers: Record<string, string>;
  readonly attributes: Record<string, string>;
  readonly nameNormalized: string | null;
  readonly confidence: number;
}

/** Empty-but-valid rulebook, so callers can build one field-by-field. */
export function makeRulebook(name: string, partial: Partial<DomainRulebook> = {}): DomainRulebook {
  return {
    name,
    signals: partial.signals ?? [],
    identifierPatterns: partial.identifierPatterns ?? {},
    brandPatterns: partial.brandPatterns ?? [],
    attributePatterns: partial.attributePatterns ?? {},
    stopWords: partial.stopWords ?? [],
    normalization: partial.normalization ?? {},
  };
}

/** Escape a literal for use inside a RegExp (the `re.escape` analogue). */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Compile a rulebook's patterns. Mirrors Python's `DomainRulebook.compile`:
 * every pattern is case-insensitive, and an invalid pattern is SKIPPED (Python
 * logs a warning) rather than throwing -- one bad user regex must not take down
 * the whole rulebook.
 */
export function compileRulebook(rb: DomainRulebook): CompiledRulebook {
  const invalid: string[] = [];
  const identifiers = new Map<string, RegExp>();
  for (const [name, pattern] of Object.entries(rb.identifierPatterns)) {
    try {
      identifiers.set(name, new RegExp(pattern, "i"));
    } catch {
      invalid.push(`identifier:${name}`);
    }
  }
  const attributes = new Map<string, RegExp>();
  for (const [name, pattern] of Object.entries(rb.attributePatterns)) {
    try {
      attributes.set(name, new RegExp(pattern, "i"));
    } catch {
      invalid.push(`attribute:${name}`);
    }
  }
  let brands: RegExp | null = null;
  if (rb.brandPatterns.length > 0) {
    const alternation = rb.brandPatterns.map(escapeRegExp).join("|");
    try {
      brands = new RegExp(`\\b(${alternation})\\b`, "i");
    } catch {
      invalid.push("brands");
    }
  }
  return { rulebook: rb, identifiers, attributes, brands, invalid };
}

/**
 * Extract brand / identifiers / attributes / a normalized name from `text`.
 *
 * Faithful to Python's scoring: brand and each matched identifier add 1 signal,
 * each matched attribute adds 0.5, a non-empty normalized name adds 1, and
 * `confidence = min(1, signals / max(identifierPatterns + 1, 2))`.
 */
export function extractWithRulebook(
  compiled: CompiledRulebook,
  text: string,
): RulebookExtraction {
  const rb = compiled.rulebook;
  let signals = 0;

  let brand: string | null = null;
  if (compiled.brands) {
    const m = compiled.brands.exec(text);
    if (m) {
      brand = (m[1] ?? m[0]).trim();
      signals += 1;
    }
  }

  const identifiers: Record<string, string> = {};
  for (const [name, re] of compiled.identifiers) {
    const m = re.exec(text);
    if (m) {
      // Python: `m.group(1) if m.lastindex else m.group(0)`.
      identifiers[name] = (m[1] ?? m[0]).trim();
      signals += 1;
    }
  }

  const attributes: Record<string, string> = {};
  for (const [name, re] of compiled.attributes) {
    const m = re.exec(text);
    if (m) {
      attributes[name] = m[0].trim(); // Python uses group(0) for attributes
      signals += 0.5;
    }
  }

  // Name normalization: lowercase, strip the parts already extracted, drop
  // punctuation, then drop stop words and 1-char tokens.
  let name = text.toLowerCase();
  for (const re of compiled.identifiers.values()) name = name.replace(stripAll(re), " ");
  for (const re of compiled.attributes.values()) name = name.replace(stripAll(re), " ");
  const stop = new Set(rb.stopWords);
  // `\p{L}\p{N}_` keeps this Unicode-aware like Python's `\w` (JS `\w` is ASCII).
  const words = name
    .replace(/[^\p{L}\p{N}_\s]/gu, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1 && !stop.has(w));
  const nameNormalized = words.length > 0 ? words.join(" ").trim() : null;
  if (nameNormalized) signals += 1;

  const denom = Math.max(Object.keys(rb.identifierPatterns).length + 1, 2);
  return {
    brand,
    identifiers,
    attributes,
    nameNormalized,
    confidence: Math.min(1, signals / denom),
  };
}

/** A global-flag twin of `re`, so `.replace` strips EVERY occurrence (Python's `pattern.sub`). */
function stripAll(re: RegExp): RegExp {
  return re.global ? re : new RegExp(re.source, `${re.flags}g`);
}

/**
 * Best-matching rulebook for a set of column names, scored by how many of a
 * rulebook's `signals` appear in the joined lowercased column names. Returns
 * `null` when nothing scores above zero (Python's `match_domain`).
 */
export function matchDomain(
  columns: readonly string[],
  rulebooks: readonly DomainRulebook[],
): DomainRulebook | null {
  if (rulebooks.length === 0) return null;
  const colStr = columns.map((c) => c.toLowerCase()).join(" ");
  let best: DomainRulebook | null = null;
  let bestScore = 0;
  for (const rb of rulebooks) {
    let score = 0;
    for (const s of rb.signals) if (colStr.includes(s.toLowerCase())) score += 1;
    if (score > bestScore) {
      bestScore = score;
      best = rb;
    }
  }
  return bestScore > 0 ? best : null;
}
