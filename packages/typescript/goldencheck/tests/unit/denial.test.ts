import { describe, it, expect } from "vitest";
import { TabularData } from "../../src/core/data.js";
import { discoverDenialConstraints } from "../../src/core/denial/mine.js";

/** Build a status/order_day/ship_day frame where shipped orders never ship
 *  before they were ordered — the invariant ¬(status='shipped' ∧ order_day>ship_day). */
function shippingFrame(n: number): TabularData {
  const statuses = ["shipped", "pending", "cancelled"];
  const rows = [];
  for (let i = 0; i < n; i++) {
    const status = statuses[i % 3]!;
    const orderDay = 1 + ((i * 7) % 300);
    // shipped → ship on/after order; others → arbitrary (may precede)
    const shipDay = status === "shipped" ? orderDay + (i % 30) : 1 + ((i * 11) % 340);
    rows.push({ order_id: i + 1, status, region: ["n", "s", "e", "w"][i % 4]!, order_day: orderDay, ship_day: shipDay });
  }
  return new TabularData(rows);
}

describe("discoverDenialConstraints", () => {
  it("discovers the shipped-order invariant ¬(status='shipped' ∧ order_day > ship_day)", () => {
    const data = shippingFrame(180);
    const dcs = discoverDenialConstraints(data);
    const rendered = dcs.map((d) => d.render());
    expect(rendered).toContain("¬(status = 'shipped' ∧ order_day > ship_day)");
  });

  it("returns nothing below MIN_ROWS (100)", () => {
    const data = shippingFrame(50);
    expect(discoverDenialConstraints(data)).toEqual([]);
  });

  it("require_order_comparison=true (default) suppresses pure all-equality DCs", () => {
    // status uniquely determines category → a strict all-equality relationship,
    // but no order predicate, so it must NOT be reported by default.
    const rows = [];
    for (let i = 0; i < 150; i++) {
      const status = ["shipped", "pending", "cancelled"][i % 3]!;
      const category = status === "shipped" ? "A" : status === "pending" ? "B" : "C";
      rows.push({ status, category });
    }
    const data = new TabularData(rows);
    const dcs = discoverDenialConstraints(data);
    // Every reported DC must carry at least one order comparison.
    for (const dc of dcs) {
      expect(dc.render()).toMatch(/[<≤>≥]/);
    }
  });

  it("require_order_comparison=false surfaces equality DCs that default-on hides", () => {
    const rows = [];
    for (let i = 0; i < 150; i++) {
      const status = ["shipped", "pending", "cancelled"][i % 3]!;
      const category = status === "shipped" ? "A" : status === "pending" ? "B" : "C";
      rows.push({ status, category });
    }
    const data = new TabularData(rows);
    const withEq = discoverDenialConstraints(data, { requireOrderComparison: false });
    const defaultDcs = discoverDenialConstraints(data);
    expect(withEq.length).toBeGreaterThanOrEqual(defaultDcs.length);
  });

  it("g1 stays within eps for every reported DC", () => {
    const data = shippingFrame(180);
    const dcs = discoverDenialConstraints(data, { minConfidence: 0.95 });
    for (const dc of dcs) expect(dc.g1).toBeLessThanOrEqual(0.05 + 1e-9);
  });

  it("max_constraints caps the number of DCs", () => {
    const data = shippingFrame(180);
    const capped = discoverDenialConstraints(data, { maxConstraints: 2 });
    expect(capped.length).toBeLessThanOrEqual(2);
  });
});
