import type { ColumnValue, DomainPack } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

function mlsNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return v.trim().toUpperCase();
  });
}

registerTransform(
  { name: "mls_normalize", inputTypes: ["string"], priority: 50, mode: "series" },
  mlsNormalize,
);

export const PACK: DomainPack = {
  name: "real_estate",
  description: "Address parsing (USPS), MLS ID normalization, price cleaning",
  transforms: ["mls_normalize", "address_standardize", "zip_normalize", "currency_strip"],
  defaultConfig: makeConfig({
    transforms: [
      { column: "mls_id", ops: ["mls_normalize"] },
      { column: "address", ops: ["strip", "address_standardize"] },
      { column: "price", ops: ["currency_strip"] },
      { column: "zip", ops: ["zip_normalize"] },
    ],
  }),
};
