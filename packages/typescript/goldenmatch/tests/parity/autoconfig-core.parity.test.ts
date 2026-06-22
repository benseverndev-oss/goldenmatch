/**
 * Cross-surface parity: the TS wasm autoconfig core vs the SAME golden vectors
 * that the Rust crate (`tests/golden.rs`) and the Python oracle
 * (`scripts/gen_autoconfig_golden.py`) assert. Rust + Python + TS all green on
 * identical JSON == one decision core, three surfaces, zero drift.
 *
 * The vectors are snake_case serde JSON. We drive the RAW JSON-in/JSON-out core
 * (`decidePlanRawJson` / `classifyColumnsRawJson`) so the comparison is verbatim
 * against `expected` with no adapter in the way; a second block exercises the
 * camelCase public API (`decidePlan` / `classifyColumns`) through the adapter.
 *
 * Fixtures are copied from the rust crate's `golden/` by
 * `scripts/build_autoconfig_wasm.mjs`.
 */
import { describe, it, expect } from "vitest";
import plannerVectors from "./fixtures/autoconfig/planner_vectors.json" with { type: "json" };
import classifierVectors from "./fixtures/autoconfig/classifier_vectors.json" with { type: "json" };
import extrapolationVectors from "./fixtures/autoconfig/extrapolation_vectors.json" with { type: "json" };
import sparseMatchFloorVectors from "./fixtures/autoconfig/sparse_match_floor_vectors.json" with { type: "json" };
import exactMatchkeyFloorVectors from "./fixtures/autoconfig/exact_matchkey_floor_vectors.json" with { type: "json" };
import {
  decidePlan,
  classifyColumns,
  extrapolatePairCount,
  sparseMatchFloor,
  exactMatchkeyFloor,
  decidePlanRawJson,
  classifyColumnsRawJson,
  extrapolatePairCountRawJson,
  sparseMatchFloorRawJson,
  exactMatchkeyFloorRawJson,
  type PlannerInput,
  type CoreColumnStats,
  type ExtrapolationInput,
} from "../../src/core/autoconfigWasm.js";

const CONF_TOL = 1e-9;

interface PlannerVector {
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
}
interface ClassifierVector {
  input: Record<string, unknown>;
  expected: Record<string, unknown> & { confidence: number };
}
interface ExtrapolationVector {
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
}
interface SparseMatchFloorVector {
  input: { estimated_pairs: number };
  expected: { floor: number };
}
interface ExactMatchkeyFloorVector {
  input: { col_type: string };
  expected: { floor: number };
}

/** Exact deep-equal except `confidence`, compared with an abs tolerance. */
function expectProfileEqual(
  actual: Record<string, unknown>,
  expected: Record<string, unknown> & { confidence: number },
): void {
  const { confidence: aConf, ...aRest } = actual as { confidence: number };
  const { confidence: eConf, ...eRest } = expected;
  expect(aRest).toEqual(eRest);
  expect(Math.abs(aConf - eConf)).toBeLessThan(CONF_TOL);
}

describe("autoconfig core parity — planner (raw JSON)", () => {
  const vectors = plannerVectors as PlannerVector[];
  it("has the expected vector count", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(40);
  });
  for (const [i, v] of vectors.entries()) {
    const rule = (v.expected as { rule_name?: string }).rule_name ?? "?";
    it(`vector ${i} (${rule})`, () => {
      const out = JSON.parse(decidePlanRawJson(JSON.stringify(v.input)));
      expect(out).toEqual(v.expected);
    });
  }
});

describe("autoconfig core parity — classifier (raw JSON)", () => {
  const vectors = classifierVectors as ClassifierVector[];
  it("has the expected vector count", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(30);
  });
  for (const [i, v] of vectors.entries()) {
    const ct = (v.expected as { col_type?: string }).col_type ?? "?";
    it(`vector ${i} (${ct})`, () => {
      const out = JSON.parse(classifyColumnsRawJson(JSON.stringify([v.input])));
      expect(Array.isArray(out)).toBe(true);
      expectProfileEqual(out[0], v.expected);
    });
  }
});

describe("autoconfig core parity — extrapolation (raw JSON)", () => {
  const vectors = extrapolationVectors as ExtrapolationVector[];
  it("has the expected vector count", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(30);
  });
  for (const [i, v] of vectors.entries()) {
    it(`vector ${i}`, () => {
      const out = JSON.parse(
        extrapolatePairCountRawJson(JSON.stringify(v.input)),
      );
      expect(out).toEqual(v.expected);
    });
  }
});

describe("autoconfig core parity — sparse-match floor (raw JSON)", () => {
  const vectors = sparseMatchFloorVectors as SparseMatchFloorVector[];
  it("has the expected vector count", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(15);
  });
  for (const [i, v] of vectors.entries()) {
    it(`vector ${i} (estimated_pairs=${v.input.estimated_pairs})`, () => {
      const out = JSON.parse(
        sparseMatchFloorRawJson(JSON.stringify(v.input)),
      );
      expect(out).toEqual(v.expected);
    });
  }
});

describe("autoconfig core parity — exact-matchkey floor (raw JSON)", () => {
  const vectors = exactMatchkeyFloorVectors as ExactMatchkeyFloorVector[];
  it("has the expected vector count", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(13);
  });
  for (const [i, v] of vectors.entries()) {
    it(`vector ${i} (col_type=${v.input.col_type || "<empty>"})`, () => {
      const out = JSON.parse(
        exactMatchkeyFloorRawJson(JSON.stringify(v.input)),
      );
      expect(out).toEqual(v.expected);
    });
  }
});

describe("autoconfig core parity — camelCase adapter round-trips", () => {
  it("decidePlan adapts snake<->camel and matches the planner golden", () => {
    const vectors = plannerVectors as PlannerVector[];
    for (const v of vectors) {
      const inp = v.input as {
        n_rows_full: number;
        estimated_pair_count: number;
        runtime: {
          available_ram_gb: number;
          cpu_count: number;
          disk_free_gb: number;
        };
        caps: {
          bucket_available: boolean;
          ray_available: boolean;
          ray_auto_select: boolean;
          user_backend: string | null;
        };
      };
      const camelIn: PlannerInput = {
        nRowsFull: inp.n_rows_full,
        estimatedPairCount: inp.estimated_pair_count,
        runtime: {
          availableRamGb: inp.runtime.available_ram_gb,
          cpuCount: inp.runtime.cpu_count,
          diskFreeGb: inp.runtime.disk_free_gb,
        },
        caps: {
          bucketAvailable: inp.caps.bucket_available,
          rayAvailable: inp.caps.ray_available,
          rayAutoSelect: inp.caps.ray_auto_select,
          userBackend: inp.caps.user_backend as PlannerInput["caps"]["userBackend"],
        },
      };
      const plan = decidePlan(camelIn);
      const exp = v.expected as Record<string, unknown>;
      expect(plan.backend).toBe(exp.backend);
      expect(plan.chunkSize).toBe(exp.chunk_size);
      expect(plan.maxWorkers).toBe(exp.max_workers);
      expect(plan.pairSpillThreshold).toBe(exp.pair_spill_threshold);
      expect(plan.clusteringStrategy).toBe(exp.clustering_strategy);
      expect(plan.ruleName).toBe(exp.rule_name);
    }
  });

  it("extrapolatePairCount adapts snake<->camel and matches the golden", () => {
    const vectors = extrapolationVectors as ExtrapolationVector[];
    for (const v of vectors) {
      const inp = v.input as {
        total_comparisons: number;
        n_blocks: number;
        singleton_block_count: number;
        chao1_f1: number | null;
        chao1_f2: number | null;
        n_rows_sample: number;
        n_rows_full: number;
      };
      const camelIn: ExtrapolationInput = {
        totalComparisons: inp.total_comparisons,
        nBlocks: inp.n_blocks,
        singletonBlockCount: inp.singleton_block_count,
        chao1F1: inp.chao1_f1,
        chao1F2: inp.chao1_f2,
        nRowsSample: inp.n_rows_sample,
        nRowsFull: inp.n_rows_full,
      };
      const out = extrapolatePairCount(camelIn);
      const exp = v.expected as Record<string, unknown>;
      expect(out.nBlocks).toBe(exp.n_blocks);
      expect(out.totalComparisons).toBe(exp.total_comparisons);
      expect(out.singletonBlockCount).toBe(exp.singleton_block_count);
    }
  });

  it("sparseMatchFloor matches the golden", () => {
    const vectors = sparseMatchFloorVectors as SparseMatchFloorVector[];
    for (const v of vectors) {
      expect(sparseMatchFloor(v.input.estimated_pairs)).toBe(v.expected.floor);
    }
  });

  it("exactMatchkeyFloor matches the golden", () => {
    const vectors = exactMatchkeyFloorVectors as ExactMatchkeyFloorVector[];
    for (const v of vectors) {
      expect(exactMatchkeyFloor(v.input.col_type)).toBe(v.expected.floor);
    }
  });

  it("classifyColumns adapts snake<->camel and matches the classifier golden", () => {
    const vectors = classifierVectors as ClassifierVector[];
    const cols: CoreColumnStats[] = vectors.map((v) => {
      const inp = v.input as {
        name: string;
        dtype: string;
        sample_values: string[];
        null_rate: number;
        cardinality_ratio: number;
        avg_len: number;
      };
      return {
        name: inp.name,
        dtype: inp.dtype,
        sampleValues: inp.sample_values,
        nullRate: inp.null_rate,
        cardinalityRatio: inp.cardinality_ratio,
        avgLen: inp.avg_len,
      };
    });
    const profiles = classifyColumns(cols);
    expect(profiles.length).toBe(vectors.length);
    profiles.forEach((p, i) => {
      const exp = vectors[i]!.expected as Record<string, unknown> & {
        confidence: number;
      };
      expect(p.colType).toBe(exp.col_type);
      expect(p.needsLlmEscalation).toBe(exp.needs_llm_escalation);
      expect(p.nullRate).toBe(exp.null_rate);
      expect(p.cardinalityRatio).toBe(exp.cardinality_ratio);
      expect(p.avgLen).toBe(exp.avg_len);
      expect(Math.abs(p.confidence - exp.confidence)).toBeLessThan(CONF_TOL);
    });
  });
});
