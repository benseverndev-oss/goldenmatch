import { describe, it, expect } from "vitest";
import {
  PatternTypeScorer,
  classifyField,
} from "../../src/core/scorers/pattern-type.js";
import { makeFieldInfo } from "../../src/core/types.js";

const field = (name: string, samples: string[]) =>
  makeFieldInfo({ name, sampleValues: samples });

describe("classifyField", () => {
  it("detects email", () => {
    expect(
      classifyField(field("x", ["a@b.co", "foo@bar.com", "qux@zap.io"]))
    ).toBe("email");
  });

  it("detects uuid", () => {
    expect(
      classifyField(
        field("x", [
          "550e8400-e29b-41d4-a716-446655440000",
          "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
          "6ba7b811-9dad-11d1-80b4-00c04fd430c8",
        ])
      )
    ).toBe("uuid");
  });

  it("detects iso date, ipv4, url, zip_us, currency", () => {
    expect(classifyField(field("x", ["2024-01-01", "2026-12-31"]))).toBe("date_iso");
    expect(classifyField(field("x", ["10.0.0.1", "192.168.1.1"]))).toBe("ip_v4");
    expect(
      classifyField(field("x", ["https://example.com", "http://foo.bar"]))
    ).toBe("url");
    expect(classifyField(field("x", ["90210", "10001-1234"]))).toBe("zip_us");
    expect(classifyField(field("x", ["$9.99", "$100,000.00"]))).toBe("currency");
  });

  it("returns null below threshold", () => {
    expect(
      classifyField(
        field("x", ["a@b.co", "not-an-email", "also not", "nope", "???"])
      )
    ).toBeNull();
  });

  it("returns null with no samples", () => {
    expect(classifyField(field("x", []))).toBeNull();
  });
});

describe("PatternTypeScorer", () => {
  const scorer = new PatternTypeScorer();

  it("abstains when either side has no samples", () => {
    expect(
      scorer.score(
        field("a", []),
        field("b", ["a@b.co", "c@d.co"])
      )
    ).toBeNull();
  });

  it("matches identical semantic types", () => {
    const r = scorer.score(
      field("a", ["a@b.co", "c@d.co"]),
      field("b", ["x@y.co", "p@q.co"])
    );
    expect(r).not.toBeNull();
    expect(r!.score).toBeGreaterThan(0.5);
    expect(r!.reasoning).toContain("email");
  });

  it("scores 0 on semantic type mismatch", () => {
    const r = scorer.score(
      field("a", ["a@b.co", "c@d.co"]),
      field("b", ["2024-01-01", "2026-12-31"])
    );
    expect(r!.score).toBe(0);
    expect(r!.reasoning).toContain("mismatch");
  });

  it("scores 0 when no type detected on either side", () => {
    const r = scorer.score(
      field("a", ["gibberish", "nonsense"]),
      field("b", ["foo", "bar"])
    );
    expect(r!.score).toBe(0);
  });
});
