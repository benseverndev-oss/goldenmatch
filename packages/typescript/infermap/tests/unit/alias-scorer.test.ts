import { describe, it, expect } from "vitest";
import { AliasScorer } from "../../src/core/scorers/alias.js";
import { makeFieldInfo } from "../../src/core/types.js";

const f = (name: string, metadata: Record<string, unknown> = {}) =>
  makeFieldInfo({ name, metadata });

describe("AliasScorer", () => {
  const scorer = new AliasScorer();

  it("matches aliases to canonical", () => {
    const r = scorer.score(f("fname"), f("first_name"));
    expect(r).not.toBeNull();
    expect(r!.score).toBe(0.95);
    expect(r!.reasoning).toContain("first_name");
  });

  it("matches two aliases sharing a canonical", () => {
    const r = scorer.score(f("mobile"), f("telephone"));
    expect(r!.score).toBe(0.95);
  });

  it("abstains when neither field is known and target has no declared aliases", () => {
    const r = scorer.score(f("xyz_col_1"), f("abc_col_2"));
    expect(r).toBeNull();
  });

  it("scores 0 when one side is known and the other is not", () => {
    const r = scorer.score(f("email"), f("random_field"));
    expect(r!.score).toBe(0);
  });

  it("honors schema-declared aliases on the target", () => {
    const r = scorer.score(
      f("cust_email"),
      f("customer_email", { aliases: ["cust_email", "contact_e"] })
    );
    expect(r!.score).toBe(0.95);
    expect(r!.reasoning).toContain("declared alias");
  });

  it("is case-insensitive and trim-insensitive", () => {
    const r = scorer.score(f("  FNAME "), f("First_Name"));
    expect(r!.score).toBe(0.95);
  });

  it("accepts user-provided alias extensions", () => {
    const custom = new AliasScorer({ order_id: ["orderNum", "ord_no"] });
    const r = custom.score(f("ord_no"), f("order_id"));
    expect(r!.score).toBe(0.95);
  });
});
