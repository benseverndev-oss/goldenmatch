import { describe, it, expect } from "vitest";
import {
  makeNegativeEvidenceField,
  makeMatchkeyConfig,
  makeMatchkeyField,
  type NegativeEvidenceField,
  type WeightedMatchkey,
  type ExactMatchkey,
} from "../../src/core/types.js";

describe("makeNegativeEvidenceField", () => {
  it("applies v1.11 defaults", () => {
    const ne = makeNegativeEvidenceField({ field: "phone", scorer: "exact" });
    expect(ne.transforms).toEqual([]);
    expect(ne.threshold).toBe(0.5);
    expect(ne.penalty).toBe(0.5);
    expect(ne.field).toBe("phone");
    expect(ne.scorer).toBe("exact");
  });

  it("preserves explicit overrides", () => {
    const ne = makeNegativeEvidenceField({
      field: "email",
      scorer: "token_sort",
      transforms: ["lowercase"],
      threshold: 0.4,
      penalty: 0.3,
    });
    expect(ne.transforms).toEqual(["lowercase"]);
    expect(ne.threshold).toBe(0.4);
    expect(ne.penalty).toBe(0.3);
  });
});

describe("makeMatchkeyConfig negativeEvidence round-trip", () => {
  it("weighted matchkey carries negativeEvidence through", () => {
    const ne: NegativeEvidenceField = makeNegativeEvidenceField({
      field: "phone",
      scorer: "exact",
      transforms: ["digits_only"],
    });
    const mk = makeMatchkeyConfig({
      name: "test_w",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name" })],
      threshold: 0.85,
      negativeEvidence: [ne],
    }) as WeightedMatchkey;
    expect(mk.type).toBe("weighted");
    expect(mk.negativeEvidence).toBeDefined();
    expect(mk.negativeEvidence?.length).toBe(1);
    expect(mk.negativeEvidence?.[0]?.field).toBe("phone");
  });

  it("exact matchkey carries negativeEvidence + threshold (Path Y)", () => {
    const ne: NegativeEvidenceField = makeNegativeEvidenceField({
      field: "last_name",
      scorer: "token_sort",
    });
    const mk = makeMatchkeyConfig({
      name: "test_e",
      type: "exact",
      fields: [makeMatchkeyField({ field: "email" })],
      negativeEvidence: [ne],
      threshold: 0.5,
    }) as ExactMatchkey;
    expect(mk.type).toBe("exact");
    expect(mk.negativeEvidence?.[0]?.field).toBe("last_name");
    expect(mk.threshold).toBe(0.5);
  });

  it("omitting negativeEvidence leaves matchkey without the key", () => {
    const mk = makeMatchkeyConfig({
      name: "no_ne",
      type: "weighted",
      fields: [makeMatchkeyField({ field: "name" })],
    });
    expect(("negativeEvidence" in mk) && (mk as WeightedMatchkey).negativeEvidence).toBeFalsy();
  });
});
