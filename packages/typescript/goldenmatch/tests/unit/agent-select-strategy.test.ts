import { describe, it, expect } from "vitest";
import { selectStrategy } from "../../src/core/agent/strategy.js";
import type { DataProfile, FieldProfile } from "../../src/core/agent/types.js";

const f = (name: string, o: Partial<FieldProfile>): FieldProfile => ({
  name,
  type: "string",
  uniqueness: 0.5,
  null_rate: 0,
  avg_length: 8,
  ...o,
});
const prof = (
  fields: FieldProfile[],
  extra: Partial<DataProfile> = {},
): DataProfile => ({
  row_count: 1000,
  fields,
  has_sensitive: false,
  ...extra,
});

describe("selectStrategy decision table", () => {
  it("sensitive -> pprl, auto_execute false", () => {
    const d = selectStrategy(prof([f("ssn", {})], { has_sensitive: true }));
    expect(d.strategy).toBe("pprl");
    expect(d.auto_execute).toBe(false);
  });

  it("strong id only -> exact_only", () => {
    const d = selectStrategy(
      prof([f("id", { uniqueness: 0.99, null_rate: 0.0 })]),
    );
    expect(d.strategy).toBe("exact_only");
    expect(d.strong_ids).toEqual(["id"]);
    expect(d.fuzzy_fields).toEqual([]);
    expect(d.auto_execute).toBe(true);
  });

  it("strong id + fuzzy -> exact_then_fuzzy", () => {
    const d = selectStrategy(
      prof([
        f("id", { uniqueness: 0.99, null_rate: 0.0 }),
        f("name", { uniqueness: 0.4, avg_length: 8, null_rate: 0.1 }),
      ]),
    );
    expect(d.strategy).toBe("exact_then_fuzzy");
    expect(d.strong_ids).toEqual(["id"]);
    expect(d.fuzzy_fields).toEqual(["name"]);
  });

  it("fuzzy only -> fuzzy", () => {
    const d = selectStrategy(
      prof([f("name", { uniqueness: 0.4, avg_length: 8, null_rate: 0.1 })]),
    );
    expect(d.strategy).toBe("fuzzy");
    expect(d.fuzzy_fields).toEqual(["name"]);
  });

  it("no usable fields -> fuzzy fallback", () => {
    // not strong (null_rate too high), not fuzzy (uniqueness > 0.9)
    const d = selectStrategy(
      prof([f("x", { uniqueness: 0.95, null_rate: 0.9 })]),
    );
    expect(d.strategy).toBe("fuzzy");
    expect(d.why).toMatch(/defaulting to fuzzy/i);
    // fallback fuzzy_fields = all string fields
    expect(d.fuzzy_fields).toEqual(["x"]);
  });

  it("domain recognized, no strong/fuzzy -> domain_extraction", () => {
    // person domain: first_name(2) + last_name(2) + email(2) = score 6 ->
    // detectDomain confidence 0.6 > 0.5. Each field is NON-strong
    // (null_rate 0.1 >= 0.05) and NON-fuzzy (uniqueness 0.95 > 0.90).
    const d = selectStrategy(
      prof([
        f("first_name", { uniqueness: 0.95, null_rate: 0.1 }),
        f("last_name", { uniqueness: 0.95, null_rate: 0.1 }),
        f("email", { uniqueness: 0.95, null_rate: 0.1 }),
      ]),
    );
    expect(d.strategy).toBe("domain_extraction");
    expect(d.domain).not.toBeNull();
    expect(d.domain).toBe("person");
  });

  it("backend=ray above 500k rows", () => {
    const d = selectStrategy(
      prof([f("id", { uniqueness: 0.99, null_rate: 0 })], {
        row_count: 600_000,
      }),
    );
    expect(d.backend).toBe("ray");
  });

  it("no backend at or below 500k rows", () => {
    const d = selectStrategy(
      prof([f("id", { uniqueness: 0.99, null_rate: 0 })], {
        row_count: 500_000,
      }),
    );
    expect(d.backend).toBeNull();
  });
});
