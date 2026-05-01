// Shared runtime types for the TS benchmark runner.
// Mirrors the dataclasses in benchmark/runners/python/infermap_bench/.
import type { SchemaInfo } from "infermap";

export type Category = "valentine" | "real_world" | "synthetic";
export type Difficulty = "easy" | "medium" | "hard";

export interface CaseSource {
  name: string;
  url: string;
  license: string;
  attribution: string;
}

export interface CaseRef {
  id: string;
  path: string;
  category: Category;
  subcategory: string;
  source: CaseSource;
  tags: string[];
  expectedDifficulty: Difficulty;
  fieldCounts: { source: number; target: number };
}

export interface Expected {
  mappings: Array<{ source: string; target: string }>;
  unmappedSource: string[];
  unmappedTarget: string[];
}

export interface CaseData {
  id: string;
  category: string;
  subcategory: string;
  tags: string[];
  expectedDifficulty: string;
  sourceSchema: SchemaInfo;
  targetSchema: SchemaInfo;
  expected: Expected;
}
