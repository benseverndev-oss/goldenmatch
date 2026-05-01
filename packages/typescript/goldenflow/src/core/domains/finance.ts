import type { ColumnValue, DomainPack } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

function accountMask(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = v.replace(/\D/g, "");
    if (digits.length < 4) return v;
    return "*".repeat(digits.length - 4) + digits.slice(-4);
  });
}

function cusipFormat(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return v.trim().toUpperCase().slice(0, 9);
  });
}

registerTransform(
  { name: "account_mask", inputTypes: ["string"], priority: 50, mode: "series" },
  accountMask,
);
registerTransform(
  { name: "cusip_format", inputTypes: ["string"], priority: 50, mode: "series" },
  cusipFormat,
);

export const PACK: DomainPack = {
  name: "finance",
  description: "Account masking, currency standardization, CUSIP/ISIN formatting",
  transforms: ["account_mask", "cusip_format", "currency_strip", "date_iso8601"],
  defaultConfig: makeConfig({
    transforms: [
      { column: "account_number", ops: ["account_mask"] },
      { column: "amount", ops: ["currency_strip"] },
      { column: "transaction_date", ops: ["date_iso8601"] },
    ],
  }),
};
