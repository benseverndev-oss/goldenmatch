/**
 * audit.ts -- byte-identical TS port of Python `identity/audit.py` (#1078).
 *
 * Tamper-evidence for the append-only identity event log, in two layers:
 *   1. per-event content hash (`eventContentHash`) computed at insert time;
 *   2. an on-demand seal chain (`sealAuditLog` / `verifyAuditChain`) that folds
 *      every event's content hash, in `eventId` order, into a chained root.
 *
 * The whole point of this port is CROSS-VERIFICATION: a seal (or entry hash)
 * computed here must be BYTE-IDENTICAL to Python's, so a `.goldenmatch/
 * identity.db` written by either toolkit verifies under the other. That hinges
 * on reproducing Python's `json.dumps(canon, sort_keys=True,
 * separators=(",",":"))` (default `ensure_ascii=True`) EXACTLY -- see
 * `canonicalJson` below. Do NOT swap in `JSON.stringify`; it diverges on key
 * ordering, non-ASCII escaping, and float repr (`1.0` vs `1`).
 *
 * Edge-safe: uses Web Crypto (`crypto.subtle`) + `TextEncoder`, no `node:*`.
 * Hashing is async (not a hot path).
 */
import type { IdentityEvent, AuditSeal, IdentityStore } from "./types.js";
import { pyIsoformat } from "./pyDatetime.js";

/**
 * Wrapper marking a JS number that must serialize as a PYTHON FLOAT (trailing
 * `.0` when integral), matching how Python's `json.dumps` renders a `float`.
 * JS collapses `1.0` to the integer `1`; Python keeps `1.0`. `trust` is the one
 * canon field that is a Python float, so it is wrapped; `previousClaimId` is a
 * Python `int` and stays a plain number (integer repr).
 */
class PyFloat {
  constructor(readonly value: number) {}
}

/** Reproduce Python `round(x, 6)` for the trust field (values are 0..1, ≤6dp
 * in practice, so this is effectively identity; kept for faithfulness). */
function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

/** Escape a string the way Python `json.dumps(..., ensure_ascii=True)` does:
 * `"`/`\` escaped, the five short control escapes, every other codepoint
 * `< 0x20` or `> 0x7E` as lowercase `\uXXXX` (astral chars as their UTF-16
 * surrogate halves, exactly as Python emits them). */
function encodeString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c === 0x08) out += "\\b";
    else if (c === 0x09) out += "\\t";
    else if (c === 0x0a) out += "\\n";
    else if (c === 0x0c) out += "\\f";
    else if (c === 0x0d) out += "\\r";
    else if (c < 0x20 || c > 0x7e) out += "\\u" + c.toString(16).padStart(4, "0");
    else out += s[i];
  }
  return out + '"';
}

/** Render a JS number as Python's `json.dumps` would render the corresponding
 * Python value. Integral -> integer repr (`5`); otherwise JS shortest repr,
 * which matches Python's float repr for the values that occur here. */
function encodeNumber(n: number): string {
  if (!Number.isFinite(n)) {
    // Python json.dumps emits NaN/Infinity/-Infinity as bare tokens; these
    // should never appear in a canon, but mirror the behavior rather than throw.
    if (Number.isNaN(n)) return "NaN";
    return n > 0 ? "Infinity" : "-Infinity";
  }
  return String(n);
}

/**
 * Serialize a value byte-identically to Python
 * `json.dumps(value, sort_keys=True, separators=(",",":"))` with the default
 * `ensure_ascii=True`. Objects sort keys by Unicode codepoint at every depth;
 * separators are compact; strings are ASCII-escaped; a `PyFloat` renders as a
 * Python float.
 */
function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (value instanceof PyFloat) {
    const n = value.value;
    if (!Number.isFinite(n)) return encodeNumber(n);
    return Number.isInteger(n) ? `${n}.0` : String(n);
  }
  const t = typeof value;
  if (t === "boolean") return value ? "true" : "false";
  if (t === "number") return encodeNumber(value as number);
  if (t === "string") return encodeString(value as string);
  if (Array.isArray(value)) {
    return "[" + value.map((v) => canonicalJson(v)).join(",") + "]";
  }
  if (t === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort(); // JS default sort = Unicode codepoint
    const parts: string[] = [];
    for (const k of keys) {
      parts.push(encodeString(k) + ":" + canonicalJson(obj[k]));
    }
    return "{" + parts.join(",") + "}";
  }
  // Fallback mirrors Python's `default=str` (e.g. an unexpected type).
  return encodeString(String(value));
}

async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const view = new Uint8Array(digest);
  let hex = "";
  for (let i = 0; i < view.length; i++) hex += view[i]!.toString(16).padStart(2, "0");
  return hex;
}

/** Stable string form of a timestamp, matching Python `_normalize_dt`: a
 * `Date` -> `pyIsoformat` (Python `datetime.isoformat`), else `String(value)`. */
function normalizeDt(value: unknown): string {
  if (value instanceof Date) return pyIsoformat(value);
  return String(value);
}

/**
 * sha256 over an event's immutable content fields -- byte-identical to Python
 * `event_content_hash`. Excludes `eventId` and `entryHash` itself. `actor`/
 * `trust`/`recordedAt` are ALWAYS present (nullable); the claim-authority
 * fields are added ONLY when set (adding them as null would rehash every
 * provenance-free event and break existing seals).
 */
export async function eventContentHash(event: IdentityEvent): Promise<string> {
  const canon: Record<string, unknown> = {
    entity_id: event.entityId,
    kind: String(event.kind),
    payload: event.payload ?? null,
    run_name: event.runName ?? null,
    dataset: event.dataset ?? null,
    actor: event.actor ?? null,
    trust:
      event.trust !== undefined && event.trust !== null
        ? new PyFloat(round6(event.trust))
        : null,
    recorded_at: normalizeDt(event.recordedAt),
  };
  if (event.claimType !== undefined && event.claimType !== null) {
    canon["claim_type"] = String(event.claimType);
  }
  if (event.evidenceRef !== undefined && event.evidenceRef !== null) {
    canon["evidence_ref"] = String(event.evidenceRef);
  }
  if (event.previousClaimId !== undefined && event.previousClaimId !== null) {
    // Python `int(...)` -> integer repr (no decimal).
    canon["previous_claim_id"] = Math.trunc(event.previousClaimId);
  }
  const blob = canonicalJson(canon);
  return sha256Hex(blob);
}

/** The stored `entryHash` if present, else computed on the fly (covers rows
 * written before the hash column existed). Mirrors Python `_effective_hash`. */
async function effectiveHash(event: IdentityEvent): Promise<string> {
  return event.entryHash ?? (await eventContentHash(event));
}

/** One left-fold step: `acc' = sha256(acc || entryHash)`. Mirrors Python
 * `_fold_step`. A left fold means an incremental seal (seeded by the prior
 * root) yields the same root as folding the whole history from scratch. */
export async function foldStep(acc: string, entryHash: string): Promise<string> {
  return sha256Hex(acc + entryHash);
}

/** Result of `verifyAuditChain`. `ok` is the bottom line; the lists localize
 * what failed. Mirrors Python `AuditVerification`. */
export interface AuditVerification {
  ok: boolean;
  eventsChecked: number;
  sealsChecked: number;
  /** eventIds whose stored entryHash != recomputed content hash (content edit) */
  contentMismatches: number[];
  /** sealIds whose replayed root/count != stored (deletion/reorder/insertion) */
  sealMismatches: number[];
  /** sealIds whose lastEventId no longer exists in the log (sealed event deleted) */
  missingSealedEvents: number[];
}

export function verificationSummary(v: AuditVerification): string {
  if (v.ok) {
    return `audit chain intact: ${v.eventsChecked} events, ${v.sealsChecked} seals verified`;
  }
  const parts: string[] = [];
  if (v.contentMismatches.length) parts.push(`${v.contentMismatches.length} content edit(s)`);
  if (v.sealMismatches.length) parts.push(`${v.sealMismatches.length} seal mismatch(es)`);
  if (v.missingSealedEvents.length)
    parts.push(`${v.missingSealedEvents.length} sealed event(s) missing`);
  return "audit chain BROKEN: " + parts.join(", ");
}

/**
 * Anchor the current event log with a new seal and return it (or `null` when
 * there is nothing new to seal -- idempotent). Chains onto the latest seal for
 * `dataset` (`null` = global chain), folding only events appended since that
 * seal. Mirrors Python `seal_audit_log`.
 */
export async function sealAuditLog(
  store: IdentityStore,
  opts: { actor?: string | null; dataset?: string | null } = {},
): Promise<AuditSeal | null> {
  const dataset = opts.dataset ?? null;
  const actor = opts.actor ?? null;

  const prev = await store.latestSeal(dataset ?? undefined);
  const prevRoot = prev ? prev.rootHash : "";
  const prevLastId = prev && prev.lastEventId !== null ? prev.lastEventId : -1;
  const prevCount = prev ? prev.eventCount : 0;

  const all = await store.exportAuditLog(dataset ?? undefined);
  const newEvents = all.filter((e) => e.eventId !== null && e.eventId > prevLastId);
  if (newEvents.length === 0) return null;

  let acc = prevRoot;
  for (const e of newEvents) {
    acc = await foldStep(acc, await effectiveHash(e));
  }

  const seal: AuditSeal = {
    sealId: null,
    rootHash: acc,
    eventCount: prevCount + newEvents.length,
    lastEventId: newEvents[newEvents.length - 1]!.eventId,
    dataset,
    prevSealId: prev ? prev.sealId : null,
    prevRoot: prevRoot || null,
    actor,
    createdAt: new Date(0), // store assigns the real timestamp on write
  };
  const sealId = await store.addSeal(seal);
  seal.sealId = sealId;
  return seal;
}

/**
 * Replay the event log against its seal chain and report integrity. Two
 * independent checks (content edits; deletion/reorder/insertion of sealed
 * events). Mirrors Python `verify_audit_chain`.
 */
export async function verifyAuditChain(
  store: IdentityStore,
  opts: { dataset?: string | null } = {},
): Promise<AuditVerification> {
  const dataset = opts.dataset ?? null;
  const events = await store.exportAuditLog(dataset ?? undefined);
  const seals = await store.listSeals(dataset ?? undefined);

  const contentMismatches: number[] = [];
  for (const e of events) {
    if (e.entryHash !== undefined && e.entryHash !== null && e.eventId !== null) {
      if ((await eventContentHash(e)) !== e.entryHash) contentMismatches.push(e.eventId);
    }
  }

  const sealMismatches: number[] = [];
  const missingSealedEvents: number[] = [];
  const sealsSorted = [...seals].sort(
    (a, b) => (a.lastEventId ?? -1) - (b.lastEventId ?? -1),
  );
  let acc = "";
  let seen = 0;
  let sealIdx = 0;
  for (const e of events) {
    acc = await foldStep(acc, await effectiveHash(e));
    seen += 1;
    while (sealIdx < sealsSorted.length && sealsSorted[sealIdx]!.lastEventId === e.eventId) {
      const s = sealsSorted[sealIdx]!;
      if (acc !== s.rootHash || seen !== s.eventCount) {
        if (s.sealId !== null) sealMismatches.push(s.sealId);
      }
      sealIdx += 1;
    }
  }
  for (const s of sealsSorted.slice(sealIdx)) {
    if (s.sealId !== null) missingSealedEvents.push(s.sealId);
  }

  const ok =
    contentMismatches.length === 0 &&
    sealMismatches.length === 0 &&
    missingSealedEvents.length === 0;
  return {
    ok,
    eventsChecked: events.length,
    sealsChecked: seals.length,
    contentMismatches,
    sealMismatches,
    missingSealedEvents,
  };
}
