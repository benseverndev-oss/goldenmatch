// Identity-layer detection — which *parties* a frame refers to.
//
// `detectDomain` answers "how finance-y is this table". This answers "who is in
// it": a loan tape refers to a lender AND a borrower, two populations that must
// never be resolved against each other.
//
// The load-bearing reframe: a layer is a GROUP OF COLUMNS describing one party,
// not a per-column label. That makes this a labelling pass (many-to-many by
// construction), so it never routes through the deliberately 1:1 assignment
// engine, which cannot express one role spanning many columns.
//
// `infermap-core::detect_identity_layers` is the SOURCE OF TRUTH. `computeLayers`
// below is the byte-identical pure-TS fallback for when the WASM backend is not
// enabled — the same split `detect.ts` uses for `detectDomain`.
import { loadDomain } from "goldencheck-types";
import type { DomainPack, IdentityLayer, LayerDetectionResult } from "goldencheck-types";
import { DEFAULT_MIN_SCORE, detectDomain } from "./detect.js";
import {
  getInfermapBackend,
  type LayerRoleInput,
  type RawLayer,
  type RawLayerDetection,
} from "./wasm/backend.js";

const TOKEN_SPLIT = /[_\-.\s]+/;

/** A qualifier shorter than this is noise (`f_`, `x_`), not a party name.
 *  Mirrors `infermap-core::MIN_QUALIFIER_LEN`. */
const MIN_QUALIFIER_LEN = 3;

/** Universal ATTRIBUTE tokens — they describe a property of an entity, never the
 *  identity of one, in any vertical. **Mirror of `infermap-core::ATTRIBUTE_TOKENS`**;
 *  the kernel owns the list and this copy exists only for the no-WASM path.
 *
 *  Load-bearing when no pack resolves: without it, `name` groups
 *  `widget_owner_name` with `shipper_name`, fusing two unrelated parties. */
const ATTRIBUTE_TOKENS: ReadonlySet<string> = new Set([
  "name", "names", "id", "ids", "key", "code", "codes", "num", "number",
  "date", "dt", "time", "ts", "timestamp", "year", "month", "day",
  "type", "status", "flag", "amount", "amt", "value", "val", "total",
  "count", "qty", "quantity", "desc", "description", "note", "notes",
  "address", "addr", "email", "phone", "city", "state", "zip", "country",
  "first", "last", "middle", "full", "line", "row", "col", "column",
  "created", "updated", "modified", "version", "source", "record",
  // Lineage / provenance — warehouse plumbing, never a party.
  "src", "etl", "stg", "raw", "batch", "ingested", "extracted", "loaded",
  // Audit trail — siblings of created/updated/modified above.
  "approved", "reviewed", "submitted", "verified", "deleted", "inserted",
  "processed",
  // Aggregate / unit qualifiers — a measure, not an entity.
  "avg", "mean", "median", "sum", "pct", "usd", "eur", "gbp",
]);

// Score weights — mirror of the kernel's W_* constants.
const W_BASE = 0.3;
const W_AFFIX = 0.35;
const W_ROLE = 0.25;
const W_TYPES = 0.1;

function tokens(s: string): string[] {
  return s.toLowerCase().split(TOKEN_SPLIT).filter(Boolean);
}

export interface LayersInput {
  columns: string[];
}

/** `(column index, affix position, remainder tokens)` */
type Member = [number, string, string[]];

/** Qualifier candidates for one column. Leading and trailing tokens only — a
 *  party qualifier sits at one end in practice (`lender_name`,
 *  `name_of_lender`); scanning interior tokens buys little and costs precision. */
function candidates(toks: string[]): Array<[string, string, string[]]> {
  if (toks.length === 0) return [];
  if (toks.length === 1) return [[toks[0]!, "whole", []]];
  return [
    [toks[0]!, "prefix", toks.slice(1)],
    [toks[toks.length - 1]!, "suffix", toks.slice(0, -1)],
  ];
}

/** ASCII digits, matching the kernel's `is_ascii_digit` (the documented
 *  ASCII-domain parity contract). */
function remainderIsNumeric(remainder: string[]): boolean {
  return remainder.every((t) => /^[0-9]+$/.test(t));
}

function groupIsViable(
  token: string,
  members: Member[],
  roleTokens: Map<string, number>,
): boolean {
  const recognised = roleTokens.has(token);
  if (members.length < 2) return recognised;
  const distinct = new Set<string>();
  for (const [, , rem] of members) {
    if (rem.length > 0 && !remainderIsNumeric(rem)) distinct.add(JSON.stringify(rem));
  }
  return distinct.size >= 2 || recognised;
}

function typeCorroboration(members: Member[], role: LayerRoleInput | null): number {
  if (!role || role.typical_type_hints.length === 0) return 0;
  const expected = new Set<string>();
  for (const hint of role.typical_type_hints) for (const t of tokens(hint)) expected.add(t);
  if (expected.size === 0) return 0;
  let hits = 0;
  for (const [, , rem] of members) if (rem.some((t) => expected.has(t))) hits++;
  return hits / members.length;
}

/** Why a layer was proposed, or that it fell short. `low_confidence` overrides
 *  the evidence reason so a marginal layer is visible as marginal — still
 *  returned, with columns and evidence intact, rather than dropped. */
function reasonFor(
  affixStrength: number,
  roleMatched: boolean,
  score: number,
  minScore: number,
): string {
  if (score < minScore) return "low_confidence";
  if (affixStrength > 0) return roleMatched ? "affix+role_hint" : "affix";
  return roleMatched ? "role_hint" : "singleton";
}

/** Fold the kernel's flat layer record into the public `IdentityLayer`.
 *
 *  The single place evidence is assembled, shared by the WASM and pure paths —
 *  if each built its own, the two surfaces could drift in a way no kernel
 *  parity test would see. */
export function toResult(
  raw: RawLayerDetection,
  domain: string | null,
): LayerDetectionResult {
  return {
    layers: raw.layers.map((l) => ({
      role: l.role,
      kind: l.kind as IdentityLayer["kind"],
      columns: l.columns,
      score: l.score,
      reason: l.reason as IdentityLayer["reason"],
      evidence: l.qualifier
        ? {
            qualifier: l.qualifier,
            positions: l.positions,
            n_columns: l.columns.length,
            role_matched: l.role_matched,
            type_corroboration: l.type_corroboration,
          }
        : { note: "no party qualifiers found; treating frame as one population" },
    })),
    unassigned: raw.unassigned,
    domain,
  };
}

/** Byte-identical reference for `infermap-core::detect_identity_layers`.
 *
 *  Scores are UNROUNDED on purpose: `round()` differs between Python's banker's
 *  rounding, Rust's half-away-from-zero and JS `Math.round`, so rounding here
 *  would manufacture a cross-language divergence. */
export function computeLayers(
  columns: string[],
  roles: LayerRoleInput[],
  typeHints: string[],
  minScore: number,
): RawLayerDetection {
  if (columns.length === 0) {
    return { layers: [], unassigned: [] };
  }

  // token -> index into `roles`; first declaration wins.
  const roleTokens = new Map<string, number>();
  roles.forEach((r, i) => {
    for (const hint of r.name_hints) {
      for (const tok of tokens(hint)) if (!roleTokens.has(tok)) roleTokens.set(tok, i);
    }
  });

  // Field-type tokens must not open a party (`account_number`/`account_id` share
  // `account`). ROLE DECLARATIONS WIN — finance lists `payee` among the
  // `merchant` type's hints while `payee` is also a declared role, and without
  // this precedence the explicit declaration loses to an incidental overlap.
  const stop = new Set<string>(ATTRIBUTE_TOKENS);
  for (const hint of typeHints) for (const tok of tokens(hint)) stop.add(tok);
  for (const t of roleTokens.keys()) stop.delete(t);

  const groups = new Map<string, Member[]>();
  columns.forEach((col, idx) => {
    for (const [tok, position, remainder] of candidates(tokens(col))) {
      if (tok.length < MIN_QUALIFIER_LEN || stop.has(tok)) continue;
      const bucket = groups.get(tok);
      if (bucket) bucket.push([idx, position, remainder]);
      else groups.set(tok, [[idx, position, remainder]]);
    }
  });

  interface Scored {
    token: string;
    role: LayerRoleInput | null;
    members: Member[];
    score: number;
    corroboration: number;
  }
  const scored: Scored[] = [];
  for (const [token, members] of groups) {
    if (!groupIsViable(token, members, roleTokens)) continue;
    const roleIdx = roleTokens.get(token);
    const role = roleIdx === undefined ? null : roles[roleIdx]!;
    const corroboration = typeCorroboration(members, role);
    const affixStrength = Math.min(1, (members.length - 1) / 2);
    const score = Math.min(
      1,
      W_BASE + W_AFFIX * affixStrength + (role ? W_ROLE : 0) + W_TYPES * corroboration,
    );
    scored.push({ token, role, members, score, corroboration });
  }
  scored.sort(
    (a, b) =>
      b.score - a.score ||
      b.members.length - a.members.length ||
      (a.token < b.token ? -1 : a.token > b.token ? 1 : 0),
  );

  const layers: RawLayer[] = [];
  const claimed = new Set<number>();
  for (const s of scored) {
    const kept = s.members.filter(([idx]) => !claimed.has(idx));
    if (kept.length === 0) continue;
    // Re-check viability after losing columns to a stronger layer.
    if (kept.length < 2 && !roleTokens.has(s.token)) continue;
    for (const [idx] of kept) claimed.add(idx);
    const affixStrength = Math.min(1, (kept.length - 1) / 2);
    layers.push({
      role: s.role ? s.role.name : "unknown",
      kind: s.role ? s.role.kind : "unknown",
      columns: kept.map(([idx]) => columns[idx]!),
      score: s.score,
      reason: reasonFor(affixStrength, s.role !== null, s.score, minScore),
      qualifier: s.token,
      positions: Array.from(new Set(kept.map(([, p]) => p))).sort(),
      role_matched: s.role !== null,
      type_corroboration: s.corroboration,
    });
  }

  if (layers.length === 0) {
    // No party qualifiers anywhere. The honest reading is one homogeneous
    // population, not "no entities".
    return {
      layers: [
        {
          role: "unknown",
          kind: "unknown",
          columns: [...columns],
          score: 0.5,
          reason: "singleton",
          qualifier: "",
          positions: [],
          role_matched: false,
          type_corroboration: 0,
        },
      ],
      unassigned: [],
    };
  }

  layers.sort(
    (a, b) =>
      b.score - a.score ||
      (a.role < b.role ? -1 : a.role > b.role ? 1 : 0) ||
      (a.qualifier < b.qualifier ? -1 : a.qualifier > b.qualifier ? 1 : 0),
  );

  const assigned = new Set(layers.flatMap((l) => l.columns));
  return { layers, unassigned: columns.filter((c) => !assigned.has(c)) };
}

/** Flatten a pack into the kernel's plain-list inputs.
 *
 *  Host-side by design: pack loading and `typical_types` resolution are host
 *  concerns, so the kernel never learns about YAML. Roles are emitted in
 *  declaration order because the kernel resolves token collisions
 *  first-declaration-wins. */
/** Pack consulted for role vocabulary in addition to the detected vertical.
 *  Host-side policy, not a kernel concern — the kernel is handed a role list
 *  and does not know where it came from. */
const FALLBACK_DOMAIN = "generic";

/** Append the cross-vertical party vocabulary to a pack's own roles.
 *
 *  A union, not a fallback. Two gaps closed at once, both measured on real
 *  schemas:
 *
 *  - `detectDomain` finds no vertical for the overwhelming majority of frames,
 *    which loaded NO pack at all — detection grouped columns into parties
 *    correctly and then had nothing to NAME them with.
 *  - A frame that DOES resolve to a vertical still contains parties that
 *    vertical never enumerates: `finance` declares lender / borrower / payee
 *    but no plain `customer`, so `customer_id`/`customer_name` came back
 *    `unknown` on a table the pack otherwise understood well.
 *
 *  Generic parties are cross-vertical by definition — a finance table still has
 *  customers — so they are additive everywhere rather than a substitute for a
 *  vertical vocabulary.
 *
 *  **The vertical wins collisions.** Pack roles keep their position at the
 *  front, and a generic role whose NAME the pack already declares is dropped;
 *  the kernel resolves token collisions first-declaration-wins over this order.
 *
 *  Mirrors `infermap.layers._with_generic_roles`. */
export function withGenericRoles(roles: LayerRoleInput[]): LayerRoleInput[] {
  let generic: DomainPack | null = null;
  try {
    generic = loadDomain(FALLBACK_DOMAIN);
  } catch {
    return roles;
  }
  if (!generic) return roles;
  const declared = new Set(roles.map((r) => r.name));
  const { roles: genericRoles } = packInputs(generic);
  return roles.concat(genericRoles.filter((r) => !declared.has(r.name)));
}

export function packInputs(
  pack: DomainPack | null,
): { roles: LayerRoleInput[]; typeHints: string[] } {
  if (!pack) return { roles: [], typeHints: [] };
  const roles: LayerRoleInput[] = [];
  for (const role of Object.values(pack.roles ?? {})) {
    const typicalHints: string[] = [];
    for (const typeName of role.typical_types) {
      const spec = pack.types[typeName];
      if (!spec) continue;
      typicalHints.push(spec.name, ...spec.name_hints);
    }
    roles.push({
      name: role.name,
      kind: role.kind,
      name_hints: [...role.name_hints],
      typical_type_hints: typicalHints,
    });
  }
  const typeHints: string[] = [];
  for (const spec of Object.values(pack.types)) {
    typeHints.push(...spec.name_hints, spec.name);
  }
  return { roles, typeHints };
}

/** Detect the identity layers (parties) present in a frame.
 *
 *  Reads column names only. `domain` pins the domain pack; omitted, it is
 *  auto-detected via `detectDomain`. When no pack resolves, affix clustering
 *  still runs — the primary signal is domain-free by design. */
export function detectIdentityLayers(
  input: LayersInput | { records?: ReadonlyArray<Record<string, unknown>> },
  domain?: string | null,
  minScore: number = DEFAULT_MIN_SCORE,
): LayerDetectionResult {
  let columns: string[];
  if ("columns" in input && Array.isArray(input.columns)) {
    columns = input.columns;
  } else if ("records" in input && input.records && input.records.length > 0) {
    columns = Object.keys(input.records[0]!);
  } else {
    return { layers: [], unassigned: [], domain: null };
  }

  const resolvedDomain =
    domain !== undefined && domain !== null ? domain : detectDomain({ columns });

  let pack: DomainPack | null = null;
  if (resolvedDomain) {
    try {
      pack = loadDomain(resolvedDomain);
    } catch {
      // An unknown domain name degrades to affix-only detection rather than
      // failing the call — the primary signal does not need a pack.
      pack = null;
    }
  }
  const { roles, typeHints } = packInputs(pack);
  const allRoles = withGenericRoles(roles);

  const backend = getInfermapBackend();
  const raw = backend
    ? backend.detectIdentityLayers(columns, allRoles, typeHints, minScore)
    : computeLayers(columns, allRoles, typeHints, minScore);

  return toResult(raw, resolvedDomain ?? null);
}
