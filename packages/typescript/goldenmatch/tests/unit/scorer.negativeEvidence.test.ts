import { describe, it, expect } from "vitest";
import { findFuzzyMatches } from "../../src/core/scorer.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type Row,
} from "../../src/core/types.js";

describe("findFuzzyMatches with negativeEvidence", () => {
  const rows: Row[] = [
    { __row_id__: 0, name: "Alice Smith", phone: "555-1111" } as Row,
    { __row_id__: 1, name: "Alice Smith", phone: "555-1111" } as Row,
    { __row_id__: 2, name: "Alice Smith", phone: "555-9999" } as Row,
  ];

  it("no NE: both name-twins pair", () => {
    const mk = makeMatchkeyConfig({
      name: "w",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
      threshold: 0.9,
    });
    const out = findFuzzyMatches(rows, mk);
    expect(out.length).toBe(3); // (0,1) (0,2) (1,2) all match name exactly
  });

  it("NE on phone drops phone-disagreeing pairs", () => {
    const mk = makeMatchkeyConfig({
      name: "w",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name", scorer: "exact" })],
      threshold: 0.9,
      negativeEvidence: [
        makeNegativeEvidenceField({
          field: "phone",
          scorer: "exact",
          threshold: 0.5,
          penalty: 0.5,
        }),
      ],
    });
    const out = findFuzzyMatches(rows, mk);
    // Only (0,1) survives: agreement on phone. (0,2) and (1,2) get penalty 0.5 → 0.5 < 0.9.
    expect(out.length).toBe(1);
    expect(out[0]?.idA).toBe(0);
    expect(out[0]?.idB).toBe(1);
    expect(out[0]?.score).toBeCloseTo(1.0, 6);
  });

  it("NE with small penalty preserves pair when adjusted score still above threshold", () => {
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
          penalty: 0.2,
        }),
      ],
    });
    const out = findFuzzyMatches(rows, mk);
    // (0,2): 1.0 - 0.2 = 0.8 > 0.5 — kept
    expect(out.length).toBe(3);
    const p02 = out.find((p) => p.idA === 0 && p.idB === 2);
    expect(p02?.score).toBeCloseTo(0.8, 6);
  });
});
