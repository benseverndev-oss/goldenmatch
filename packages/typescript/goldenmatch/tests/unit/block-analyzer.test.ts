import { describe, it, expect } from "vitest";

import {
  analyzeBlocking,
  generateCandidates,
  scoreCandidate,
  detectColumnType,
  type BlockingSuggestion,
} from "../../src/core/block-analyzer.js";
import type { Row } from "../../src/core/types.js";

// Five surnames sharing the first 3 letters ("smi") + three unrelated rows.
// Blocking on name[:3] collapses the five into ONE oversized block.
const rows: Row[] = [
  { name: "Smithers", zip: "10001" },
  { name: "Smithson", zip: "10002" },
  { name: "Smithy", zip: "10003" },
  { name: "Smithe", zip: "10004" },
  { name: "Smithton", zip: "10005" },
  { name: "Jones", zip: "20001" },
  { name: "Brown", zip: "30002" },
  { name: "Davies", zip: "40003" },
];

describe("block-analyzer: detectColumnType", () => {
  it("classifies by name heuristic (parity with Python detect_column_type)", () => {
    expect(detectColumnType("first_name")).toBe("name");
    expect(detectColumnType("zip_code")).toBe("zip");
    expect(detectColumnType("email_addr")).toBe("email");
    expect(detectColumnType("phone")).toBe("phone");
    expect(detectColumnType("state")).toBe("state");
    expect(detectColumnType("widget")).toBe("generic");
  });
});

describe("block-analyzer: scoreCandidate", () => {
  it("reports an oversized block via max_group_size (the oversized-block warning)", () => {
    const nameCand = generateCandidates(["name"]).find(
      (c) => c.description === "name[:3]",
    )!;
    // target_block_size = 3, but the "smi" block holds 5 records -> oversized.
    const m = scoreCandidate(rows, nameCand, 3);
    expect(m.max_group_size).toBe(5);
    expect(m.max_group_size).toBeGreaterThan(3); // exceeds target => oversized
    // sum of n*(n-1)/2 = C(5,2)=10 for the one non-singleton block.
    expect(m.total_comparisons).toBe(10);
    expect(m.group_count).toBe(4); // "smi" + jon + bro + dav
  });

  it("returns zero metrics when a key field is absent", () => {
    const cand = generateCandidates(["missing_col"])[0]!;
    const m = scoreCandidate(rows, cand, 5000);
    expect(m.group_count).toBe(0);
    expect(m.score).toBe(0);
  });
});

describe("block-analyzer: analyzeBlocking", () => {
  const suggestions: BlockingSuggestion[] = analyzeBlocking(
    rows,
    ["name", "zip"],
    1000,
    3,
  );

  it("returns ranked suggestions with the Python BlockingSuggestion shape", () => {
    expect(suggestions.length).toBeGreaterThan(0);
    const s = suggestions[0]!;
    // Field names match Python's asdict(BlockingSuggestion).
    expect(s).toHaveProperty("keys");
    expect(s).toHaveProperty("group_count");
    expect(s).toHaveProperty("max_group_size");
    expect(s).toHaveProperty("mean_group_size");
    expect(s).toHaveProperty("total_comparisons");
    expect(s).toHaveProperty("estimated_recall");
    expect(s).toHaveProperty("score");
    expect(s).toHaveProperty("description");
    // keys is a list of candidate dicts {key_fields, transforms, description}.
    expect(Array.isArray(s.keys)).toBe(true);
    expect(s.keys[0]).toHaveProperty("key_fields");
    expect(s.keys[0]).toHaveProperty("transforms");
    expect(s.keys[0]).toHaveProperty("description");
  });

  it("surfaces the oversized name[:3] block in its suggestion", () => {
    const nameSug = suggestions.find((s) => s.description === "name[:3]")!;
    expect(nameSug).toBeDefined();
    expect(nameSug.max_group_size).toBe(5);
    expect(nameSug.max_group_size).toBeGreaterThan(3);
  });

  it("sorts suggestions by score descending", () => {
    for (let i = 1; i < suggestions.length; i++) {
      expect(suggestions[i - 1]!.score).toBeGreaterThanOrEqual(suggestions[i]!.score);
    }
  });
});
