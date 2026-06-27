import { describe, it, expect } from "vitest";
import {
  buildColumnSignals,
  type ColumnSignal,
} from "../../src/core/suggestColumnSignals.js";
import {
  makeConfig,
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type Row,
} from "../../src/core/types.js";

const SIGNAL_KEYS: ReadonlyArray<keyof ColumnSignal> = [
  "field",
  "col_type",
  "scorer",
  "in_blocking",
  "in_negative_evidence",
  "identity_score",
  "corruption_score",
  "collision_rate",
  "cardinality_ratio",
  "null_rate",
  "variant_rate",
];

describe("buildColumnSignals", () => {
  const rows: Row[] = [
    { id: "1", note: null, blk: "x" },
    { id: "2", note: null, blk: "x" },
  ];
  const config = makeConfig({
    blocking: {
      strategy: "static",
      keys: [{ fields: ["blk"], transforms: [] }],
      maxBlockSize: 5000,
      skipOversized: false,
    },
    matchkeys: [
      makeMatchkeyConfig({
        name: "mk",
        type: "weighted",
        fields: [makeMatchkeyField({ field: "id", scorer: "jaro_winkler" })],
        threshold: 0.85,
        negativeEvidence: [
          makeNegativeEvidenceField({ field: "note", scorer: "exact" }),
        ],
      }),
    ],
  });

  it("emits one snake_case signal per data column", () => {
    const signals = buildColumnSignals(rows, [], config);
    expect(signals.map((s) => s.field).sort()).toEqual(["blk", "id", "note"]);
    for (const s of signals) {
      expect(Object.keys(s).sort()).toEqual([...SIGNAL_KEYS].sort());
      expect(s.variant_rate).toBe(0.0);
    }
  });

  it("computes null_rate / cardinality_ratio / in_blocking / in_negative_evidence", () => {
    const byField = Object.fromEntries(
      buildColumnSignals(rows, [], config).map((s) => [s.field, s]),
    );

    // fully-null column -> null_rate 1.0, cardinality_ratio 0.0
    expect(byField["note"]!.null_rate).toBe(1.0);
    expect(byField["note"]!.cardinality_ratio).toBe(0.0);
    // negative-evidence field
    expect(byField["note"]!.in_negative_evidence).toBe(true);

    // unique column -> cardinality_ratio 1.0
    expect(byField["id"]!.cardinality_ratio).toBe(1.0);
    expect(byField["id"]!.null_rate).toBe(0.0);
    expect(byField["id"]!.scorer).toBe("jaro_winkler");

    // blocking column
    expect(byField["blk"]!.in_blocking).toBe(true);
    expect(byField["id"]!.in_blocking).toBe(false);
  });

  it("computes collision_rate over multi-member string clusters", () => {
    // two-member cluster where `blk` disagrees -> collision_rate 1.0 for blk.
    const colRows: Row[] = [
      { name: "Alice", blk: "x" },
      { name: "Alice", blk: "y" },
    ];
    const signals = buildColumnSignals(
      colRows,
      [{ members: [0, 1], size: 2, oversized: false }],
      makeConfig({ matchkeys: [] }),
    );
    const byField = Object.fromEntries(signals.map((s) => [s.field, s]));
    expect(byField["blk"]!.collision_rate).toBe(1.0);
    expect(byField["name"]!.collision_rate).toBe(0.0);
  });
});
