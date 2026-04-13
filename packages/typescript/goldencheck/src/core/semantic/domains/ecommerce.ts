import type { TypeDef } from "../../types.js";

export const ECOMMERCE_TYPES: Readonly<Record<string, TypeDef>> = {
  sku: { nameHints: ["sku", "product_code", "item_code", "upc", "ean"], valueSignals: { min_unique_pct: 0.80 }, suppress: ["cardinality", "pattern_consistency"] },
  order_id: { nameHints: ["order_id", "order_number", "order_num", "purchase_id"], valueSignals: { min_unique_pct: 0.95 }, suppress: ["cardinality", "pattern_consistency", "drift_detection"] },
  tracking_number: { nameHints: ["tracking", "shipment_id", "waybill"], valueSignals: { min_unique_pct: 0.90 }, suppress: ["pattern_consistency", "cardinality", "drift_detection"] },
  product_category: { nameHints: ["category", "product_category", "department", "product_type"], valueSignals: { max_unique: 50 }, suppress: ["uniqueness", "range_distribution"] },
  order_status: { nameHints: ["order_status", "fulfillment_status", "delivery_status"], valueSignals: { max_unique: 15 }, suppress: ["uniqueness", "range_distribution"] },
  shipping_method: { nameHints: ["shipping_method", "carrier", "delivery_method"], valueSignals: { max_unique: 15 }, suppress: ["uniqueness", "range_distribution"] },
  coupon_code: { nameHints: ["coupon", "promo", "discount_code", "voucher"], valueSignals: { short_strings: true }, suppress: ["uniqueness", "pattern_consistency"] },
  product_name: { nameHints: ["product_name", "item_name", "product_title"], valueSignals: { mixed_case: true }, suppress: ["pattern_consistency", "cardinality"] },
  customer_address: { nameHints: ["shipping_address", "billing_address", "delivery_address"], valueSignals: { avg_length_min: 15 }, suppress: ["pattern_consistency", "cardinality", "drift_detection"] },
};
