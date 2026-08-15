// The cross-vertical party vocabulary, host-side.
//
// `withGenericRoles` is HOST policy, not kernel semantics — the kernel is handed
// a role list and never learns where it came from — so it is tested per host
// rather than through the kernel-generated parity oracle. The Python sibling
// asserts the same four properties in
// `packages/python/infermap/tests/test_layers.py`.
import { loadDomain } from "goldencheck-types";
import { describe, expect, it } from "vitest";
import { detectIdentityLayers, packInputs, withGenericRoles } from "../../src/core/layers.js";

describe("generic party vocabulary", () => {
  it("names a party when no vertical is detected", () => {
    const result = detectIdentityLayers({
      columns: ["employee_id", "employee_name", "employee_email"],
    });
    expect(result.layers.map((l) => l.role)).toContain("employee");
  });

  it("unions with a detected vertical", () => {
    // finance declares lender / borrower / merchant but no plain `customer`.
    const result = detectIdentityLayers(
      { columns: ["customer_id", "customer_name", "vendor_id", "vendor_name"] },
      "finance",
    );
    const byRole = new Map(result.layers.map((l) => [l.role, l.columns]));
    expect(byRole.get("customer")).toEqual(["customer_id", "customer_name"]);
    expect(byRole.has("merchant")).toBe(true); // the vertical still wins `vendor`
    expect(byRole.has("vendor")).toBe(false);
  });

  it("keeps the vertical's roles first and drops name collisions", () => {
    const { roles } = packInputs(loadDomain("finance"));
    const names = withGenericRoles(roles).map((r) => r.name);
    expect(new Set(names).size).toBe(names.length);
    expect(names.slice(0, roles.length)).toEqual(roles.map((r) => r.name));
  });

  it("does not widen the stop-list — generic declares no field types", () => {
    expect(packInputs(loadDomain("generic")).typeHints).toEqual([]);
  });
});
