// InferMap -> Identity Graph bridge tests.
// Ports the Python cases in
// packages/python/infermap/tests/test_identity_feeder.py against the
// goldenmatch TS InMemoryIdentityStore.

import { describe, it, expect, beforeEach } from "vitest";
import {
  InMemoryIdentityStore,
  newEntityId,
  type IdentityStore,
} from "goldenmatch/core";

import {
  writeAliasesFromMapping,
  aliasWriteResultAsDict,
  DEFAULT_ALIAS_KINDS,
  DEFAULT_MIN_CONFIDENCE,
  type EntityIdResolver,
} from "../../src/node/identity.js";
import type {
  FieldMapping,
  MapResult,
  ScorerResult,
} from "../../src/core/types.js";

function makeMapping(
  pairs: ReadonlyArray<readonly [string, string]>,
  confidence = 0.95,
): MapResult {
  const mappings: FieldMapping[] = pairs.map(([source, target]) => {
    const breakdown: Record<string, ScorerResult> = {
      test: { score: confidence, reasoning: "t" },
    };
    return { source, target, confidence, breakdown, reasoning: "test fixture" };
  });
  return {
    mappings,
    unmappedSource: [],
    unmappedTarget: [],
    warnings: [],
    metadata: {},
  };
}

interface Seeded {
  store: IdentityStore;
  eidAlice: string;
  eidBob: string;
}

async function seedStore(): Promise<Seeded> {
  const store = new InMemoryIdentityStore();
  const eidAlice = newEntityId();
  const eidBob = newEntityId();
  const now = new Date();
  await store.upsertIdentity({
    entityId: eidAlice,
    status: "active",
    mergedInto: null,
    goldenRecord: null,
    confidence: null,
    dataset: "t",
    createdAt: now,
    updatedAt: now,
  });
  await store.upsertIdentity({
    entityId: eidBob,
    status: "active",
    mergedInto: null,
    goldenRecord: null,
    confidence: null,
    dataset: "t",
    createdAt: now,
    updatedAt: now,
  });
  await store.upsertRecord({
    recordId: "crm:1",
    source: "crm",
    sourcePk: "1",
    recordHash: "h1",
    entityId: eidAlice,
    payload: null,
    dataset: "t",
    firstSeenAt: now,
    lastSeenAt: now,
  });
  await store.upsertRecord({
    recordId: "crm:2",
    source: "crm",
    sourcePk: "2",
    recordHash: "h2",
    entityId: eidBob,
    payload: null,
    dataset: "t",
    firstSeenAt: now,
    lastSeenAt: now,
  });
  return { store, eidAlice, eidBob };
}

function recordEntityResolver(store: IdentityStore): EntityIdResolver {
  return async (record) => {
    const rid = `crm:${record["cust_id"]}`;
    return store.findEntityByRecord(rid);
  };
}

describe("writeAliasesFromMapping", () => {
  let seeded: Seeded;
  beforeEach(async () => {
    seeded = await seedStore();
  });

  it("writes an alias per record per alias-kind and resolves them", async () => {
    const { store, eidAlice, eidBob } = seeded;
    const records = [
      { cust_id: "1", email_addr: "alice@x.com" },
      { cust_id: "2", email_addr: "bob@y.com" },
    ];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["email_addr", "email"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm", dataset: "t" },
    );
    expect(result.aliasesWritten).toBe(4);
    expect(result.recordsProcessed).toBe(2);
    expect(result.mappingsUsed).toBe(2);

    expect(await store.resolveAlias("crm:1", "customer_id")).toBe(eidAlice);
    expect(await store.resolveAlias("crm:bob@y.com", "email")).toBe(eidBob);
  });

  it("drops low-confidence mappings", async () => {
    const { store } = seeded;
    const records = [{ cust_id: "1", email_addr: "alice@x.com" }];
    const mapping = makeMapping(
      [
        ["cust_id", "customer_id"],
        ["email_addr", "email"],
      ],
      0.5,
    );
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm", dataset: "t" },
    );
    expect(result.aliasesWritten).toBe(0);
    expect(result.mappingsUsed).toBe(0);
    expect(result.skippedLowConfidence).toBe(2);
  });

  it("skips non-alias-kind target columns (e.g. address)", async () => {
    const { store } = seeded;
    const records = [{ cust_id: "1", street: "123 Main" }];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["street", "address"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm" },
    );
    expect(result.aliasesWritten).toBe(1); // only customer_id
    expect(result.mappingsUsed).toBe(1);
  });

  it("skips records with a missing value for an alias column", async () => {
    const { store } = seeded;
    const records = [
      { cust_id: "1", email_addr: "alice@x.com" },
      { cust_id: "2" }, // no email_addr
    ];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["email_addr", "email"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm" },
    );
    expect(result.aliasesWritten).toBe(3); // alice both, bob only cust_id
    expect(result.skippedNoValue).toBe(1);
  });

  it("skips records whose resolver returns null (no failure)", async () => {
    const { store } = seeded;
    const records = [
      { cust_id: "1", email_addr: "alice@x.com" },
      { cust_id: "999", email_addr: "ghost@unknown.com" }, // no identity
    ];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["email_addr", "email"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm" },
    );
    expect(result.recordsProcessed).toBe(2);
    expect(result.aliasesWritten).toBe(2); // only alice's
    expect(result.skippedNoEntity).toBe(1);
  });

  it("honors custom alias kinds (e.g. healthcare NPI)", async () => {
    const { store, eidAlice } = seeded;
    const records = [{ cust_id: "1", provider_npi: "1234567890" }];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["provider_npi", "npi"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm", aliasKinds: new Set(["customer_id", "npi"]) },
    );
    expect(result.aliasesWritten).toBe(2);
    expect(await store.resolveAlias("crm:1234567890", "npi")).toBe(eidAlice);
  });

  it("accepts an iterable (not just a Set) for aliasKinds", async () => {
    const { store } = seeded;
    const records = [{ cust_id: "1", provider_npi: "1234567890" }];
    const mapping = makeMapping([["provider_npi", "npi"]]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm", aliasKinds: ["npi"] },
    );
    expect(result.aliasesWritten).toBe(1);
  });

  it("supports a synchronous entity-id resolver", async () => {
    const { store, eidAlice } = seeded;
    const records = [{ cust_id: "1", email_addr: "alice@x.com" }];
    const mapping = makeMapping([["email_addr", "email"]]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      () => eidAlice, // sync
      { sourceName: "crm" },
    );
    expect(result.aliasesWritten).toBe(1);
  });

  it("fails soft on a single bad addAlias and keeps going", async () => {
    const { store } = seeded;
    let calls = 0;
    const flaky: IdentityStore = {
      ...store,
      addAlias: async (a) => {
        calls += 1;
        if (calls === 1) throw new Error("boom");
        return store.addAlias(a);
      },
    } as IdentityStore;
    const records = [{ cust_id: "1", email_addr: "alice@x.com" }];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["email_addr", "email"],
    ]);
    const errors: string[] = [];
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      flaky,
      recordEntityResolver(store),
      {
        sourceName: "crm",
        onError: ({ alias }) => errors.push(alias),
      },
    );
    // 2 usable mappings; first addAlias throws, second succeeds.
    expect(result.aliasesWritten).toBe(1);
    expect(errors.length).toBe(1);
  });

  it("uses the documented defaults (min_confidence 0.85, default kinds)", () => {
    expect(DEFAULT_MIN_CONFIDENCE).toBe(0.85);
    for (const kind of ["customer_id", "email", "ssn", "ein", "doi"]) {
      expect(DEFAULT_ALIAS_KINDS.has(kind)).toBe(true);
    }
  });

  it("namespaces alias values as source_name:value", async () => {
    const { store } = seeded;
    const records = [{ cust_id: "1", email_addr: "alice@x.com" }];
    const mapping = makeMapping([["cust_id", "customer_id"]]);
    await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "salesforce" },
    );
    // Resolves under the namespaced value, not the bare value.
    expect(await store.resolveAlias("salesforce:1", "customer_id")).not.toBeNull();
    expect(await store.resolveAlias("1", "customer_id")).toBeNull();
  });

  it("throws when passed something that is not an IdentityStore", async () => {
    await expect(
      writeAliasesFromMapping(
        makeMapping([["cust_id", "customer_id"]]),
        [{ cust_id: "1" }],
        {} as IdentityStore,
        () => "fake-id",
        { sourceName: "crm" },
      ),
    ).rejects.toThrow(/IdentityStore/);
  });

  it("aliasWriteResultAsDict produces the Python snake_case shape", async () => {
    const { store } = seeded;
    const records = [{ cust_id: "1", email_addr: "alice@x.com" }];
    const mapping = makeMapping([
      ["cust_id", "customer_id"],
      ["email_addr", "email"],
    ]);
    const result = await writeAliasesFromMapping(
      mapping,
      records,
      store,
      recordEntityResolver(store),
      { sourceName: "crm" },
    );
    const d = aliasWriteResultAsDict(result);
    expect(d).toEqual({
      aliases_written: 2,
      records_processed: 1,
      mappings_used: 2,
      skipped_low_confidence: 0,
      skipped_no_value: 0,
      skipped_no_entity: 0,
      skipped_no_kind: 0,
    });
  });
});
