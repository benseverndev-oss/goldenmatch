// 03 — Engine config: reweight scorers and extend the alias table.
//
// Run: npx tsx examples/typescript/03_config_and_aliases.ts
//
// Domain-specific field names (ord_no, stock_keeping_unit, gross_amt) aren't
// in the default alias table. Pass them via config.aliases so the AliasScorer
// recognizes them. You can also override scorer weights here.

import { map } from "infermap";

const sourceRecords = [
  { ord_no: "O-1", sku: "WIDGET-A", gross_amt: "99.99", placed_ts: "2026-04-08T10:00:00Z" },
  { ord_no: "O-2", sku: "WIDGET-B", gross_amt: "149.50", placed_ts: "2026-04-08T11:30:00Z" },
  { ord_no: "O-3", sku: "GADGET-C", gross_amt: "25.00", placed_ts: "2026-04-08T12:15:00Z" },
];

const targetRecords = [
  { order_id: "", stock_keeping_unit: "", amount: "", created_at: "" },
];

const result = map(
  { records: sourceRecords },
  { records: targetRecords },
  {
    config: {
      // Teach the AliasScorer about our domain vocabulary
      aliases: {
        order_id: ["ord_no", "order_num", "order_number"],
        stock_keeping_unit: ["sku", "product_sku", "item_sku"],
        amount: ["gross_amt", "total", "gross_amount"],
        created_at: ["placed_ts", "placed_at", "order_date"],
      },
      // Reweight scorers — pull LLM out and boost pattern detection
      scorers: {
        LLMScorer: { enabled: false },
        PatternTypeScorer: { weight: 0.9 },
      },
    },
  }
);

console.log("Mappings with custom aliases:");
for (const m of result.mappings) {
  console.log(`  ${m.source.padEnd(10)} → ${m.target.padEnd(20)} (${m.confidence.toFixed(3)})`);
}
