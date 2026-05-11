import { describe, it, expect } from "vitest";
import {
  applyNegativeEvidence,
  applyNegativeEvidenceToExactPairs,
  promoteNegativeEvidence,
  pickScorerForColumn,
} from "../../src/core/autoconfigNegativeEvidence.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type GoldenMatchConfig,
  type WeightedMatchkey,
  type ExactMatchkey,
  type Row,
} from "../../src/core/types.js";
import type { ColumnPrior } from "../../src/core/complexityProfile.js";

describe("pickScorerForColumn", () => {
  it("dispatches by name substring", () => {
    expect(pickScorerForColumn("phone")).toEqual({
      transforms: ["digits_only"],
      scorer: "exact",
    });
    expect(pickScorerForColumn("email_addr")).toEqual({
      transforms: [],
      scorer: "token_sort",
    });
    expect(pickScorerForColumn("street_address")).toEqual({
      transforms: [],
      scorer: "token_sort",
    });
    expect(pickScorerForColumn("first_name")).toEqual({
      transforms: [],
      scorer: "ensemble",
    });
  });
  it("dispatches by col_type when name does not match", () => {
    expect(pickScorerForColumn("foo", "date")).toEqual({
      transforms: [],
      scorer: "exact",
    });
    expect(pickScorerForColumn("foo", "phone")).toEqual({
      transforms: ["digits_only"],
      scorer: "exact",
    });
  });
});

describe("applyNegativeEvidence", () => {
  const mk = makeMatchkeyConfig({
    name: "w",
    type: "weighted",
    fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
    threshold: 0.5,
    negativeEvidence: [
      makeNegativeEvidenceField({
        field: "phone",
        scorer: "exact",
        threshold: 0.5,
        penalty: 0.4,
      }),
    ],
  });

  it("returns 0 when matchkey has no negative evidence", () => {
    const bare = makeMatchkeyConfig({
      name: "b",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name" })],
      threshold: 0.5,
    });
    const p = applyNegativeEvidence(
      bare,
      { name: "alice", phone: "111" } as Row,
      { name: "alice", phone: "222" } as Row,
    );
    expect(p).toBe(0);
  });

  it("returns 0 when NE field agrees", () => {
    const p = applyNegativeEvidence(
      mk,
      { name: "alice", phone: "111" } as Row,
      { name: "alice", phone: "111" } as Row,
    );
    expect(p).toBe(0);
  });

  it("subtracts penalty when NE field disagrees below threshold", () => {
    const p = applyNegativeEvidence(
      mk,
      { name: "alice", phone: "111" } as Row,
      { name: "alice", phone: "222" } as Row,
    );
    expect(p).toBeCloseTo(0.4, 6);
  });

  it("skips when one side is null", () => {
    const p = applyNegativeEvidence(
      mk,
      { name: "alice", phone: null } as Row,
      { name: "alice", phone: "222" } as Row,
    );
    expect(p).toBe(0);
  });
});

describe("applyNegativeEvidenceToExactPairs (Path Y)", () => {
  const mkExact = makeMatchkeyConfig({
    name: "exact_email",
    type: "exact",
    fields: [makeMatchkeyField({ field: "email", scorer: "exact" })],
    threshold: 0.5,
    negativeEvidence: [
      makeNegativeEvidenceField({
        field: "last_name",
        scorer: "token_sort",
        threshold: 0.5,
        penalty: 0.6,
      }),
    ],
  });

  const rows: Row[] = [
    { __row_id__: 0, email: "shared@x.com", last_name: "Smith" } as Row,
    { __row_id__: 1, email: "shared@x.com", last_name: "Smith" } as Row,
    { __row_id__: 2, email: "shared@x.com", last_name: "Vanderbilt" } as Row,
  ];

  it("keeps pairs where NE field agrees", () => {
    const out = applyNegativeEvidenceToExactPairs(
      [{ idA: 0, idB: 1, score: 1.0 }],
      mkExact,
      rows,
    );
    expect(out).toHaveLength(1);
    expect(out[0]?.score).toBeCloseTo(1.0, 6);
  });

  it("filters pairs where NE field disagrees (adjusted score below threshold)", () => {
    const out = applyNegativeEvidenceToExactPairs(
      [{ idA: 0, idB: 2, score: 1.0 }],
      mkExact,
      rows,
    );
    // penalty 0.6 => final 0.4 < threshold 0.5 => filtered
    expect(out).toHaveLength(0);
  });

  it("returns pairs unchanged when matchkey has no NE", () => {
    const bare = makeMatchkeyConfig({
      name: "exact_bare",
      type: "exact",
      fields: [makeMatchkeyField({ field: "email" })],
    });
    const input = [{ idA: 0, idB: 1, score: 1.0 }];
    const out = applyNegativeEvidenceToExactPairs(input, bare, rows);
    expect(out).toEqual(input);
  });
});

describe("promoteNegativeEvidence", () => {
  const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
    __row_id__: i,
    email: `u${i}@x.com`,
    phone: `${1000 + i}`,
    first_name: `Name${i}`,
  })) as Row[];

  const priors: Record<string, ColumnPrior> = {
    email: { identityScore: 0.95, corruptionScore: 0.0 },
    phone: { identityScore: 0.85, corruptionScore: 0.0 },
    first_name: { identityScore: 0.2, corruptionScore: 0.0 },
  };

  it("adds phone as NE to weighted matchkey when phone is in an exact matchkey (anchor)", () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_phone",
          type: "exact",
          fields: [makeMatchkeyField({ field: "phone" })],
        }),
        makeMatchkeyConfig({
          name: "weighted_name",
          type: "weighted",
          fields: [makeMatchkeyField({ field: "first_name" })],
          threshold: 0.8,
        }),
      ],
    };
    const out = promoteNegativeEvidence(cfg, rows, priors);
    const w = out.matchkeys?.find((m) => m.name === "weighted_name") as
      | WeightedMatchkey
      | undefined;
    expect(w?.negativeEvidence?.length).toBeGreaterThan(0);
    const fields = (w?.negativeEvidence ?? []).map((n) => n.field);
    expect(fields).toContain("phone");
    // email is in NO exact matchkey... but wait — email is not an exact MK
    // field here. Anchor gate excludes it.
    expect(fields).not.toContain("email");
  });

  it("v1.12: adds NE to exact matchkey skipping the anchor gate", () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_email",
          type: "exact",
          fields: [makeMatchkeyField({ field: "email" })],
        }),
      ],
    };
    const out = promoteNegativeEvidence(cfg, rows, priors);
    const e = out.matchkeys?.[0] as ExactMatchkey;
    // phone has identity_score 0.85 >= 0.75 and cardinality 1.0 — added.
    const fields = (e.negativeEvidence ?? []).map((n) => n.field);
    expect(fields).toContain("phone");
    // threshold default 0.5 was set on exact MK
    expect(e.threshold).toBe(0.5);
  });

  it("skips NE columns in blocking", () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_phone",
          type: "exact",
          fields: [makeMatchkeyField({ field: "phone" })],
        }),
        makeMatchkeyConfig({
          name: "w",
          type: "weighted",
          fields: [makeMatchkeyField({ field: "first_name" })],
          threshold: 0.8,
        }),
      ],
      blocking: {
        strategy: "static",
        keys: [{ fields: ["phone"], transforms: [] }],
        maxBlockSize: 5000,
        skipOversized: false,
      },
    };
    const out = promoteNegativeEvidence(cfg, rows, priors);
    const w = out.matchkeys?.find((m) => m.name === "w") as
      | WeightedMatchkey
      | undefined;
    const fields = (w?.negativeEvidence ?? []).map((n) => n.field);
    expect(fields).not.toContain("phone");
  });

  it("is idempotent (re-running adds nothing)", () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_phone",
          type: "exact",
          fields: [makeMatchkeyField({ field: "phone" })],
        }),
        makeMatchkeyConfig({
          name: "w",
          type: "weighted",
          fields: [makeMatchkeyField({ field: "first_name" })],
          threshold: 0.8,
        }),
      ],
    };
    const once = promoteNegativeEvidence(cfg, rows, priors);
    const twice = promoteNegativeEvidence(once, rows, priors);
    const w1 = once.matchkeys?.find((m) => m.name === "w") as WeightedMatchkey;
    const w2 = twice.matchkeys?.find((m) => m.name === "w") as WeightedMatchkey;
    expect((w2.negativeEvidence ?? []).length).toBe((w1.negativeEvidence ?? []).length);
  });

  it("no-op on empty rows or empty priors", () => {
    const cfg: GoldenMatchConfig = { matchkeys: [] };
    expect(promoteNegativeEvidence(cfg, [], priors)).toEqual(cfg);
    expect(promoteNegativeEvidence(cfg, rows, {})).toEqual(cfg);
  });
});
