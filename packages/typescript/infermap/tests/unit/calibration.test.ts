// Tests for src/core/calibration.ts. Mirrors tests/test_calibration.py.
import { describe, it, expect } from "vitest";
import {
  IdentityCalibrator,
  IsotonicCalibrator,
  PlattCalibrator,
  loadCalibrator,
  saveCalibrator,
} from "../../src/core/calibration.js";
import { MapEngine } from "../../src/core/engine.js";
import { makeFieldInfo, makeSchemaInfo } from "../../src/core/types.js";

// Local ECE implementation — intentionally independent of any benchmark code.
function ece(scores: number[], correct: number[], numBins = 10): number {
  if (scores.length === 0) return 0;
  const bins: Array<Array<[number, number]>> = Array.from({ length: numBins }, () => []);
  for (let i = 0; i < scores.length; i++) {
    const idx = Math.min(Math.floor(scores[i]! * numBins), numBins - 1);
    bins[idx]!.push([scores[i]!, correct[i]!]);
  }
  const total = scores.length;
  let out = 0;
  for (const bin of bins) {
    if (bin.length === 0) continue;
    let conf = 0;
    let acc = 0;
    for (const [s, c] of bin) {
      conf += s;
      acc += c;
    }
    conf /= bin.length;
    acc /= bin.length;
    out += (bin.length / total) * Math.abs(conf - acc);
  }
  return out;
}

// Deterministic LCG so tests don't depend on Math.random.
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

const fieldsFromNames = (names: string[]) =>
  names.map((n) =>
    makeFieldInfo({ name: n, dtype: "string", sampleValues: ["a", "b", "c"], valueCount: 3 })
  );

const fixtureSchemas = (): { src: ReturnType<typeof makeSchemaInfo>; tgt: ReturnType<typeof makeSchemaInfo> } => ({
  src: makeSchemaInfo({ fields: fieldsFromNames(["cust_id", "email", "amt"]) }),
  tgt: makeSchemaInfo({
    fields: fieldsFromNames(["customer_id", "email_addr", "amount", "notes"]),
  }),
});

describe("IdentityCalibrator", () => {
  it("is a passthrough", () => {
    const cal = new IdentityCalibrator();
    expect(cal.transform([0.1, 0.5, 0.9])).toEqual([0.1, 0.5, 0.9]);
  });
  it("round-trips through JSON", () => {
    const cal = new IdentityCalibrator();
    const loaded = loadCalibrator(JSON.parse(JSON.stringify(saveCalibrator(cal))));
    expect(loaded.kind).toBe("identity");
  });
});

describe("IsotonicCalibrator", () => {
  it("is monotonic", () => {
    const r = rng(1);
    const xs = Array.from({ length: 200 }, () => r());
    const ys = xs.map((p) => (r() < p ? 1 : 0));
    const cal = new IsotonicCalibrator();
    cal.fit(xs, ys);
    const probe = Array.from({ length: 50 }, (_, i) => i / 49);
    const out = cal.transform(probe);
    for (let i = 1; i < out.length; i++) {
      expect(out[i]! + 1e-9).toBeGreaterThanOrEqual(out[i - 1]!);
    }
  });

  it("reduces ECE on overconfident raw scores", () => {
    const r = rng(2);
    const trueP = Array.from({ length: 500 }, () => r());
    const raw = trueP.map((p) => Math.sqrt(p)); // overconfident
    const correct = trueP.map((p) => (r() < p ? 1 : 0));
    const before = ece(raw, correct);
    const cal = new IsotonicCalibrator();
    cal.fit(raw, correct);
    const after = ece(cal.transform(raw), correct);
    expect(after).toBeLessThan(before);
  });

  it("round-trips through JSON", () => {
    const cal = new IsotonicCalibrator();
    const r = rng(3);
    const xs = Array.from({ length: 50 }, () => r());
    const ys = xs.map((p) => (r() < p ? 1 : 0));
    cal.fit(xs, ys);
    const json = JSON.parse(JSON.stringify(saveCalibrator(cal)));
    const loaded = loadCalibrator(json) as IsotonicCalibrator;
    expect(loaded.kind).toBe("isotonic");
    const a = cal.transform(xs);
    const b = loaded.transform(xs);
    for (let i = 0; i < a.length; i++) {
      expect(b[i]).toBeCloseTo(a[i]!, 9);
    }
  });
});

describe("PlattCalibrator", () => {
  it("reduces ECE on overconfident raw scores", () => {
    const r = rng(4);
    const trueP = Array.from({ length: 500 }, () => r());
    const raw = trueP.map((p) => Math.sqrt(p));
    const correct = trueP.map((p) => (r() < p ? 1 : 0));
    const before = ece(raw, correct);
    const cal = new PlattCalibrator();
    cal.fit(raw, correct);
    const after = ece(cal.transform(raw), correct);
    expect(after).toBeLessThan(before);
  });

  it("round-trips through JSON", () => {
    const cal = new PlattCalibrator(2.5, -1);
    const json = JSON.parse(JSON.stringify(saveCalibrator(cal)));
    const loaded = loadCalibrator(json) as PlattCalibrator;
    expect(loaded.kind).toBe("platt");
    expect(loaded.a).toBeCloseTo(2.5, 9);
    expect(loaded.b).toBeCloseTo(-1, 9);
  });
});

describe("IsotonicCalibrator empty fit", () => {
  it("is a noop — passthrough on [0,1]", () => {
    const cal = new IsotonicCalibrator();
    cal.fit([], []);
    const out = cal.transform([0.3, 0.7]);
    expect(out[0]).toBeCloseTo(0.3, 5);
    expect(out[1]).toBeCloseTo(0.7, 5);
  });
});

describe("loadCalibrator error handling", () => {
  it("throws on unknown kind", () => {
    expect(() => loadCalibrator({ kind: "wat" })).toThrow();
  });
  it("throws on malformed isotonic JSON (missing x/y)", () => {
    expect(() => loadCalibrator({ kind: "isotonic" })).toThrow(/missing or non-array/);
  });
  it("throws on malformed platt JSON (missing a/b)", () => {
    expect(() => loadCalibrator({ kind: "platt" })).toThrow(/missing or non-number/);
  });
});

describe("MapEngine + calibrator invariant", () => {
  it("does not change which mappings are picked", () => {
    const { src, tgt } = fixtureSchemas();
    const base = new MapEngine().mapSchemas(src, tgt);
    const basePairs = base.mappings.map((m) => `${m.source}->${m.target}`);

    const r = rng(5);
    const xs = Array.from({ length: 100 }, () => r());
    const ys = xs.map((p) => (r() < p * p ? 1 : 0));
    const cal = new IsotonicCalibrator();
    cal.fit(xs, ys);

    const calibrated = new MapEngine({ calibrator: cal }).mapSchemas(src, tgt);
    const calPairs = calibrated.mappings.map((m) => `${m.source}->${m.target}`);
    expect(calPairs).toEqual(basePairs);
  });

  it("default (no calibrator) is bit-identical to explicit undefined", () => {
    const { src, tgt } = fixtureSchemas();
    const r1 = new MapEngine().mapSchemas(src, tgt);
    const r2 = new MapEngine({}).mapSchemas(src, tgt);
    const a = r1.mappings.map((m) => [m.source, m.target, m.confidence]);
    const b = r2.mappings.map((m) => [m.source, m.target, m.confidence]);
    expect(a).toEqual(b);
  });
});
