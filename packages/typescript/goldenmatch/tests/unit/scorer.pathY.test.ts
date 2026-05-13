import { describe, it, expect } from "vitest";
import { runDedupePipeline } from "../../src/core/pipeline.js";
import {
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeNegativeEvidenceField,
  type GoldenMatchConfig,
  type Row,
} from "../../src/core/types.js";

describe("Path Y — NE on exact matchkey via pipeline", () => {
  const rows: Row[] = [
    { __row_id__: 0, email: "shared@x.com", last_name: "Smith" } as Row,
    { __row_id__: 1, email: "shared@x.com", last_name: "Smith" } as Row,
    { __row_id__: 2, email: "shared@x.com", last_name: "Vanderbilt" } as Row,
  ];

  it("without NE: all three pair on email", async () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_email",
          type: "exact",
          fields: [makeMatchkeyField({ field: "email" })],
        }),
      ],
      blocking: {
        strategy: "static",
        keys: [{ fields: ["email"], transforms: [] }],
        maxBlockSize: 5000,
        skipOversized: false,
      },
    };
    const r = await runDedupePipeline(rows, cfg);
    expect(r.scoredPairs.length).toBe(3);
  });

  it("with NE on last_name: collision pair (0,2) and (1,2) are filtered (Path Y)", async () => {
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        makeMatchkeyConfig({
          name: "exact_email",
          type: "exact",
          fields: [makeMatchkeyField({ field: "email" })],
          threshold: 0.5,
          negativeEvidence: [
            makeNegativeEvidenceField({
              field: "last_name",
              scorer: "token_sort",
              threshold: 0.5,
              penalty: 0.6,
            }),
          ],
        }),
      ],
      blocking: {
        strategy: "static",
        keys: [{ fields: ["email"], transforms: [] }],
        maxBlockSize: 5000,
        skipOversized: false,
      },
    };
    const r = await runDedupePipeline(rows, cfg);
    // (0,1) survives (same last_name); (0,2) and (1,2) drop.
    expect(r.scoredPairs.length).toBe(1);
    expect(r.scoredPairs[0]?.idA).toBe(0);
    expect(r.scoredPairs[0]?.idB).toBe(1);
  });
});
