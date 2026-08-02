/**
 * Metric-aware attribute selection for the resolution tier (port of Python
 * `semantic/blocking.py`). `semanticFieldRoles` reads the declared
 * {keys, dimensions, measures} from each dialect; `metricAwareAttributes` turns
 * them into the ER attribute allow-list (declared dimensions, never a key or a
 * measure, with a blind fallback). The roles/selection logic is deterministic and
 * tested directly; a small end-to-end case confirms the selection is actually
 * threaded into `certifySemanticModelResolved`.
 */
import { describe, it, expect } from "vitest";

import {
  semanticFieldRoles,
  metricAwareAttributes,
  frameColumns,
  type SemanticFieldRoles,
} from "../../src/core/semantic/blocking.js";
import { certifySemanticModelResolved } from "../../src/core/semantic/certify.js";
import type { SemanticFrames } from "../../src/core/semantic/frame.js";

describe("semanticFieldRoles — declared roles per dialect", () => {
  it("MetricFlow: entities → keys, dimensions → dimensions, measures → measures", () => {
    const doc = {
      semantic_models: [
        {
          name: "orders",
          entities: [{ name: "orders", type: "primary", expr: "resolved_entity_id" }],
          dimensions: [{ name: "email", type: "categorical" }, { name: "city" }],
          measures: [{ name: "revenue", agg: "sum", expr: "revenue" }],
        },
      ],
    };
    const roles = semanticFieldRoles(doc);
    expect(roles.keys).toEqual(["resolved_entity_id"]);
    expect(roles.dimensions).toEqual(["email", "city"]);
    expect(roles.measures).toEqual(["revenue"]);
  });

  it("Cube: primary_key dimension → key, other dimensions → dimensions, measures → measures", () => {
    const doc = {
      cubes: [
        {
          name: "orders",
          sql_table: "public.orders",
          dimensions: [
            { name: "id", sql: "id", primary_key: true },
            { name: "status", sql: "status" },
          ],
          measures: [{ name: "count", type: "count" }],
        },
      ],
    };
    const roles = semanticFieldRoles(doc);
    expect(roles.keys).toEqual(["id"]);
    expect(roles.dimensions).toEqual(["status"]);
    expect(roles.measures).toEqual(["count"]);
  });

  it("OSI: primary_key → keys, non-key fields → dimensions, metrics → measures", () => {
    const doc = {
      version: "0.1",
      semantic_model: [
        {
          name: "sales",
          datasets: [
            {
              name: "orders",
              primary_key: ["id"],
              fields: [{ name: "id" }, { name: "email" }, { name: "city" }],
            },
          ],
          metrics: [{ name: "revenue", expression: "SUM(orders.amount)" }],
        },
      ],
    };
    const roles = semanticFieldRoles(doc);
    expect(roles.keys).toEqual(["id"]);
    expect(roles.dimensions).toEqual(["email", "city"]);
    expect(roles.measures).toEqual(["revenue"]);
  });

  it("de-dups columns declared more than once, preserving order", () => {
    const doc = {
      semantic_models: [
        { name: "a", dimensions: [{ name: "email" }, { name: "email" }, { name: "city" }] },
        { name: "b", dimensions: [{ name: "city" }] },
      ],
    };
    expect(semanticFieldRoles(doc).dimensions).toEqual(["email", "city"]);
  });
});

describe("metricAwareAttributes — the ER attribute allow-list", () => {
  const roles: SemanticFieldRoles = {
    keys: ["customer_id"],
    dimensions: ["email", "city"],
    measures: ["revenue"],
  };

  it("keeps declared dimensions present in the frame, in frame order", () => {
    // frame order (city before email) drives the output order, not roles order.
    const cols = ["customer_id", "revenue", "city", "email", "notes"];
    expect(metricAwareAttributes(roles, cols)).toEqual(["city", "email"]);
  });

  it("never includes a key or a measure", () => {
    const out = metricAwareAttributes(roles, ["customer_id", "revenue", "email"]);
    expect(out).not.toContain("customer_id");
    expect(out).not.toContain("revenue");
    expect(out).toEqual(["email"]);
  });

  it("blind fallback (every non-key, non-measure column) when no declared dimension is present", () => {
    const noDims: SemanticFieldRoles = { keys: ["customer_id"], dimensions: [], measures: ["revenue"] };
    const cols = ["customer_id", "revenue", "name", "phone"];
    expect(metricAwareAttributes(noDims, cols)).toEqual(["name", "phone"]);
  });

  it("blind fallback when declared dimensions are absent from the frame", () => {
    const cols = ["customer_id", "revenue", "name"]; // no email/city present
    expect(metricAwareAttributes(roles, cols)).toEqual(["name"]);
  });

  it("frameColumns returns the frame's column names", () => {
    expect(frameColumns({ a: [1], b: [2] })).toEqual(["a", "b"]);
  });
});

describe("certifySemanticModelResolved — metric-aware selection is threaded through", () => {
  it("excludes a declared measure from ER: fragmentation still found despite a differing revenue", async () => {
    // Rows 0 & 1 are the same person (same declared dimension email/name) recorded
    // under distinct customer_id, and their revenue DIFFERS. Metric-aware selection
    // resolves on the declared dimensions only (email), so the differing measure
    // can't split them -> fragmentation is detected.
    const model = {
      semantic_models: [
        {
          name: "customers",
          entities: [{ name: "customer", type: "primary", expr: "customer_id" }],
          dimensions: [{ name: "email" }, { name: "name" }],
          measures: [{ name: "revenue", agg: "sum", expr: "revenue" }],
        },
      ],
    };
    const frames: SemanticFrames = {
      customers: {
        customer_id: [1, 2, 3, 4],
        email: ["alice@x.com", "alice@x.com", "bob@y.com", "carol@z.com"],
        name: ["Alice Smith", "Alice Smith", "Bob Jones", "Carol White"],
        revenue: [10, 999999, 30, 40], // differs across the duplicate pair
      },
    };

    const report = await certifySemanticModelResolved(model, frames); // metricAware defaults on
    const cert = report.entries[0]!.certificate;
    expect(cert.resolvedEntities).not.toBeNull();
    expect(cert.fragmentedEntities as number).toBeGreaterThanOrEqual(1);
    expect(cert.undercountEstimate as number).toBeGreaterThan(0);
  }, 20000);

  it("metricAware:false falls back to blind selection and still runs", async () => {
    const model = {
      semantic_models: [
        {
          name: "customers",
          entities: [{ name: "customer", type: "primary", expr: "customer_id" }],
          dimensions: [{ name: "email" }],
          measures: [{ name: "revenue", agg: "sum", expr: "revenue" }],
        },
      ],
    };
    const frames: SemanticFrames = {
      customers: {
        customer_id: [1, 2, 3],
        email: ["a@x.com", "b@y.com", "c@z.com"],
        revenue: [10, 20, 30],
      },
    };
    const report = await certifySemanticModelResolved(model, frames, { metricAware: false });
    expect(report.nCertified).toBe(1);
    const cert = report.entries[0]!.certificate;
    // Distinct records → the blind path runs and stays estimable (fail-open keeps
    // the structural certificate intact either way); no spurious fragmentation.
    expect(cert.estimable).toBe(true);
    expect(cert.fragmentedEntities ?? 0).toBe(0);
  }, 20000);
});
