import type { ColumnValue, DomainPack } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

function skuNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return v.trim().toUpperCase().replace(/[^A-Z0-9-]/g, "");
  });
}

registerTransform(
  { name: "sku_normalize", inputTypes: ["string"], priority: 50, mode: "series" },
  skuNormalize,
);

export const PACK: DomainPack = {
  name: "ecommerce",
  description: "SKU normalization, price cleaning, category standardization",
  transforms: ["sku_normalize", "currency_strip", "category_auto_correct", "strip"],
  defaultConfig: makeConfig({
    transforms: [
      { column: "sku", ops: ["sku_normalize"] },
      { column: "price", ops: ["currency_strip"] },
      { column: "category", ops: ["strip", "title_case"] },
    ],
  }),
};
