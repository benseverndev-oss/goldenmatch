/**
 * Behavioral tests for the ER resolution tier of the key-integrity certifier —
 * the TS port of Python `certify_key_integrity(..., resolve=True)` (`_add_resolution`).
 *
 * These are BEHAVIORAL, not cross-language byte-parity: the resolve tier runs
 * `dedupe()`, whose zero-config output is engine/version-sensitive (the
 * goldenmatch-kg lesson — a toy-frame merge varies by version), so a byte-parity
 * fixture on resolution output would be flaky and meaningless across the Python
 * and TS engines. Instead we assert the invariants the tier guarantees:
 *   - two records with IDENTICAL attributes but DIFFERENT declared keys →
 *     fragmentation (one resolved entity spans >1 declared key) → undercount;
 *   - fully-distinct records → no fragmentation;
 *   - a table with no attribute columns → resolution skipped with a note;
 *   - fail-open: the structural certificate is always intact;
 *   - the certificate's `safeBound` discounts a measured undercount.
 */
import { describe, it, expect } from "vitest";

import {
  certifyKeyIntegrity,
  resolveKeyIntegrity,
} from "../../src/core/semantic/keyIntegrity.js";
import { certifySemanticModelResolved } from "../../src/core/semantic/certify.js";
import type { SemanticFrames } from "../../src/core/semantic/frame.js";

describe("resolveKeyIntegrity — ER fragmentation/undercount tier", () => {
  it("detects fragmentation: identical attributes under two distinct keys", async () => {
    // Rows 0 & 1 are the SAME real person recorded under different customer_id
    // (a fragmented entity). Rows 2 & 3 are clearly distinct people.
    const table = {
      customer_id: [1, 2, 3, 4],
      name: ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
      email: ["alice@x.com", "alice@x.com", "bob@y.com", "carol@z.com"],
      city: ["New York", "New York", "Los Angeles", "San Francisco"],
    };

    const cert = await resolveKeyIntegrity(table, { key: "customer_id" });

    // The declared key IS unique at grain (4 distinct ids over 4 rows), so the
    // structural pass sees nothing wrong — the whole point of the resolution tier.
    expect(cert.isUniqueAtGrain).toBe(true);
    expect(cert.estimate).toBe(1.0);

    expect(cert.estimable).toBe(true);
    expect(cert.resolvedEntities).not.toBeNull();
    expect(cert.resolvedEntities!).toBeGreaterThanOrEqual(1);
    expect(cert.fragmentedEntities!).toBeGreaterThanOrEqual(1);
    expect(cert.undercountEstimate!).toBeGreaterThan(0);
    // safeBound must discount the measured undercount below the clean structural estimate.
    expect(cert.safeBound).toBeLessThan(cert.estimate);
    expect(cert.safeBound).toBeCloseTo(Math.min(cert.estimate, 1 - cert.undercountEstimate!), 10);
    expect(cert.note).toContain("fragmentation");
  }, 15000);

  it("clean data: fully-distinct records → no fragmentation, undercount 0", async () => {
    const table = {
      customer_id: [1, 2, 3],
      name: ["Alice Smith", "Bob Jones", "Carol White"],
      email: ["alice@x.com", "bob@y.com", "carol@z.com"],
      city: ["New York", "Los Angeles", "San Francisco"],
    };

    const cert = await resolveKeyIntegrity(table, { key: "customer_id" });

    expect(cert.estimable).toBe(true);
    expect(cert.fragmentedEntities).toBe(0);
    expect(cert.undercountEstimate).toBe(0.0);
    // No measured undercount → safeBound collapses to the structural estimate.
    expect(cert.safeBound).toBeCloseTo(cert.estimate, 10);
  }, 15000);

  it("skips resolution when there are no attribute columns to resolve on", async () => {
    // Every column is either the key or a declared measure → nothing to resolve on.
    const table = { customer_id: [1, 2, 3], revenue: [10, 20, 30] };

    const cert = await resolveKeyIntegrity(table, {
      key: "customer_id",
      measures: ["revenue"],
    });

    expect(cert.resolvedEntities).toBeNull();
    expect(cert.fragmentedEntities).toBeNull();
    expect(cert.undercountEstimate).toBeNull();
    expect(cert.estimable).toBe(true);
    expect(cert.note).toContain("resolution skipped");
    // Structural tier is intact and safeBound collapses to the estimate.
    expect(cert.safeBound).toBeCloseTo(cert.estimate, 10);
  }, 15000);

  it("honors an explicit attributes list", async () => {
    const table = {
      customer_id: [1, 2, 3, 4],
      name: ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
      email: ["alice@x.com", "alice@x.com", "bob@y.com", "carol@z.com"],
      note_col: ["p", "q", "r", "s"],
    };
    // Resolve only on name+email (ignore note_col, which would otherwise separate
    // the two Alice records).
    const cert = await resolveKeyIntegrity(table, {
      key: "customer_id",
      attributes: ["name", "email"],
    });
    expect(cert.fragmentedEntities!).toBeGreaterThanOrEqual(1);
    expect(cert.undercountEstimate!).toBeGreaterThan(0);
  }, 15000);
});

describe("certifyKeyIntegrity (structural) — resolve fields default null", () => {
  it("leaves the resolution fields null and safeBound == estimate", () => {
    const table = { customer_id: [1, 2, 2], amt: [1, 2, 3] };
    const cert = certifyKeyIntegrity(table, { key: "customer_id", measures: ["amt"] });
    expect(cert.resolvedEntities).toBeNull();
    expect(cert.fragmentedEntities).toBeNull();
    expect(cert.undercountEstimate).toBeNull();
    expect(cert.estimable).toBe(true);
    expect(cert.safeBound).toBe(cert.estimate);
  });
});

describe("certifySemanticModelResolved — resolve tier across a semantic model", () => {
  it("populates the resolution tier for a MetricFlow model", async () => {
    const model = {
      semantic_models: [
        {
          name: "customers",
          entities: [{ name: "customer", type: "primary", expr: "customer_id" }],
          measures: [{ name: "revenue" }],
          // Declare the identity-bearing attributes as dimensions so the (default)
          // metric-aware ER resolves on name/email, not the differing `created`.
          dimensions: [{ name: "name" }, { name: "email" }, { name: "created", type: "time" }],
        },
      ],
    };
    const frames: SemanticFrames = {
      customers: {
        customer_id: [1, 2, 3, 4],
        revenue: [10, 20, 30, 40],
        name: ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
        email: ["alice@x.com", "alice@x.com", "bob@y.com", "carol@z.com"],
        created: ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
      },
    };

    const report = await certifySemanticModelResolved(model, frames);
    expect(report.dialect).toBe("metricflow");
    expect(report.nCertified).toBe(1);
    const cert = report.entries[0]!.certificate;
    // revenue is a declared measure → excluded from attribute resolution; the
    // duplicated Alice records still fragment across customer_id.
    expect(cert.resolvedEntities).not.toBeNull();
    expect(cert.fragmentedEntities!).toBeGreaterThanOrEqual(1);
    expect(cert.undercountEstimate!).toBeGreaterThan(0);
  }, 20000);
});
