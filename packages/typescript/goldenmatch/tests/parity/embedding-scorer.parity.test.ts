/**
 * embedding-scorer.parity.test.ts — gap 2 (embedding / record_embedding).
 *
 * API-PARITY, NOT GOLDEN-VALUE. Python computes `embedding` /
 * `record_embedding` similarity from Vertex/torch embeddings that the
 * edge-safe TS port cannot reproduce numerically. The parity contract here is
 * STRUCTURAL: the scorer must (a) exist (no "Unknown scorer" throw), (b) route
 * through the pluggable embedder shim, and (c) compute cosine similarity.
 * A deterministic stub embedder stands in for Vertex/torch so the suite needs
 * neither network nor native deps.
 */
import { describe, it, expect, afterEach } from "vitest";
import {
  scoreField,
  scorePair,
  setSyncEmbedder,
  getSyncEmbedder,
  cosineSimilarity,
} from "../../src/core/scorer.js";
import type { SyncTextEmbedder } from "../../src/core/scorer.js";
import type { Row, MatchkeyField } from "../../src/core/types.js";

/** Deterministic 8-dim bag-of-chars embedder (no torch / no Vertex). Stable
 *  across runs, so cosine values are reproducible. */
const stubEmbedder: SyncTextEmbedder = (text: string) => {
  const v = new Array<number>(8).fill(0);
  for (const ch of text.toLowerCase()) {
    const idx = ch.charCodeAt(0) % 8;
    v[idx] = v[idx]! + 1;
  }
  return v;
};

afterEach(() => setSyncEmbedder(null));

describe("embedding scorer — gap 2 structural parity", () => {
  it("embedding scorer EXISTS (no 'Unknown scorer' throw once embedder set)", () => {
    setSyncEmbedder(stubEmbedder);
    expect(() => scoreField("hello world", "hello there", "embedding")).not.toThrow();
    expect(() =>
      scoreField("hello world", "hello there", "record_embedding"),
    ).not.toThrow();
  });

  it("throws an actionable error when no embedder is registered", () => {
    setSyncEmbedder(null);
    expect(() => scoreField("a", "b", "embedding")).toThrow(/registered embedder/);
    expect(() => scoreField("a", "b", "record_embedding")).toThrow(
      /registered embedder/,
    );
  });

  it("routes through the embedder and returns its cosine similarity", () => {
    setSyncEmbedder(stubEmbedder);
    const a = "Acme Corporation";
    const b = "ACME corp";
    const expected = cosineSimilarity(stubEmbedder(a), stubEmbedder(b));
    const got = scoreField(a, b, "embedding");
    expect(got).not.toBeNull();
    expect(got as number).toBeCloseTo(expected, 10);
    // record_embedding shares the same field-level path.
    expect(scoreField(a, b, "record_embedding")).toBeCloseTo(expected, 10);
  });

  it("identical text scores 1.0; null operands score null", () => {
    setSyncEmbedder(stubEmbedder);
    expect(scoreField("same text", "same text", "embedding")).toBeCloseTo(1.0, 10);
    expect(scoreField(null, "x", "embedding")).toBeNull();
    expect(scoreField("x", null, "record_embedding")).toBeNull();
  });

  it("getSyncEmbedder reflects the registered shim", () => {
    expect(getSyncEmbedder()).toBeNull();
    setSyncEmbedder(stubEmbedder);
    expect(getSyncEmbedder()).toBe(stubEmbedder);
  });

  it("embedding field participates in weighted scorePair aggregation", () => {
    setSyncEmbedder(stubEmbedder);
    const rowA: Row = { name: "Acme Corporation", __row_id__: 0 };
    const rowB: Row = { name: "ACME corp", __row_id__: 1 };
    const fields: MatchkeyField[] = [
      { field: "name", transforms: ["lowercase", "strip"], scorer: "embedding", weight: 1.0 },
    ];
    const expected = cosineSimilarity(
      stubEmbedder("acme corporation"),
      stubEmbedder("acme corp"),
    );
    expect(scorePair(rowA, rowB, fields)).toBeCloseTo(expected, 10);
  });
});
