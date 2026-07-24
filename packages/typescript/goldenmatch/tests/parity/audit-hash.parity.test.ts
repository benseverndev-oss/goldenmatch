/**
 * Cross-language audit-hash parity: proves the TS identity-audit crypto is
 * BYTE-IDENTICAL to Python's, so a seal/entry-hash computed here verifies under
 * Python and vice-versa. The fixture `fixtures/identity/audit-hash.json` is
 * authored by the Python oracle
 * (`packages/python/goldenmatch/scripts/emit_audit_hash_fixture.py`) -- each
 * event's `expected_hash` is Python `event_content_hash`, and `seal.root_hash`
 * is the `_fold_step` chain. If this passes, cross-verification holds.
 *
 * NOTE the fixture's `recorded_at` values are millisecond-aligned: a JS `Date`
 * is ms-precision, so a Python SUB-ms event can't round-trip through a `Date`
 * (an inherent JS limitation, documented). Real TS-authored events are always
 * ms-precision.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, it, expect } from "vitest";
import {
  eventContentHash,
  foldStep,
  sealAuditLog,
  verifyAuditChain,
} from "../../src/core/identity/audit.js";
import { InMemoryIdentityStore } from "../../src/core/identity/in-memory-store.js";
import type { IdentityEvent } from "../../src/core/identity/types.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(HERE, "fixtures", "identity", "audit-hash.json"), "utf8"),
) as {
  events: Array<Record<string, unknown>>;
  seal: { root_hash: string; event_count: number; last_event_id: number };
};

function toEvent(e: Record<string, unknown>): IdentityEvent {
  return {
    eventId: e["event_id"] as number,
    entityId: e["entity_id"] as string,
    kind: e["kind"] as IdentityEvent["kind"],
    payload: (e["payload"] as Record<string, unknown> | null) ?? null,
    runName: (e["run_name"] as string | null) ?? null,
    dataset: (e["dataset"] as string | null) ?? null,
    actor: (e["actor"] as string | null) ?? null,
    trust: (e["trust"] as number | null) ?? null,
    claimType: (e["claim_type"] as IdentityEvent["claimType"]) ?? null,
    evidenceRef: (e["evidence_ref"] as IdentityEvent["evidenceRef"]) ?? null,
    previousClaimId: (e["previous_claim_id"] as number | null) ?? null,
    entryHash: null,
    // Naive Python isoformat string -> UTC Date (append Z; the app/store always
    // works in UTC, see sqlite-store `parseDate`).
    recordedAt: new Date((e["recorded_at"] as string) + "Z"),
  };
}

describe("identity-audit cross-language hash parity", () => {
  it("eventContentHash byte-matches Python for every fixture event", async () => {
    for (const raw of fixture.events) {
      const ev = toEvent(raw);
      const got = await eventContentHash(ev);
      expect(got, `event ${raw["event_id"]} (${raw["kind"]})`).toBe(raw["expected_hash"]);
    }
  });

  it("the seal fold reproduces Python's committed root_hash", async () => {
    let acc = "";
    for (const raw of fixture.events) acc = await foldStep(acc, raw["expected_hash"] as string);
    expect(acc).toBe(fixture.seal.root_hash);
  });

  it("sealAuditLog + verifyAuditChain agree over a store of the fixture events", async () => {
    const store = new InMemoryIdentityStore();
    // Ensure at least one entity exists so events attach cleanly; emit each
    // event verbatim (entryHash stamped at insert).
    for (const raw of fixture.events) {
      const ev = toEvent(raw);
      await store.emitEvent({ ...ev, eventId: null, entryHash: null });
    }
    const seal = await sealAuditLog(store, { actor: "test" });
    expect(seal).not.toBeNull();
    expect(seal!.eventCount).toBe(fixture.events.length);

    const ok = await verifyAuditChain(store);
    expect(ok.ok).toBe(true);
    expect(ok.eventsChecked).toBe(fixture.events.length);
    expect(ok.sealsChecked).toBe(1);
    expect(ok.contentMismatches).toEqual([]);
    expect(ok.sealMismatches).toEqual([]);
  });

  it("verifyAuditChain detects a content edit (tamper)", async () => {
    const store = new InMemoryIdentityStore();
    for (const raw of fixture.events) {
      await store.emitEvent({ ...toEvent(raw), eventId: null, entryHash: null });
    }
    await sealAuditLog(store, { actor: "test" });
    // Tamper: mutate a stored event's payload in place (entryHash now stale).
    const log = await store.exportAuditLog();
    const victim = log[2]!;
    // The in-memory store returns copies from exportAuditLog, so mutate the
    // backing array via a fresh emit of a corrupted duplicate is not the path;
    // instead re-open a store where one event's entryHash mismatches its content.
    void victim;
    // Simplest deterministic tamper: build a store, seal, then verify a store
    // whose first event carries a WRONG stored entryHash.
    const tampered = new InMemoryIdentityStore();
    for (let i = 0; i < fixture.events.length; i++) {
      const ev = toEvent(fixture.events[i]!);
      // Pre-set a bogus entryHash on one event so the stamp is skipped and the
      // stored hash disagrees with the recomputed content hash.
      const entryHash = i === 1 ? "deadbeef".repeat(8) : null;
      await tampered.emitEvent({ ...ev, eventId: null, entryHash });
    }
    await sealAuditLog(tampered, { actor: "test" });
    const bad = await verifyAuditChain(tampered);
    expect(bad.ok).toBe(false);
    expect(bad.contentMismatches.length).toBeGreaterThan(0);
  });
});
