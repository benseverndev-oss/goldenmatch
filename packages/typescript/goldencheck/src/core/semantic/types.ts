/**
 * Base type definitions — bundled as TS const (no runtime YAML).
 * Port of goldencheck/semantic/types.yaml.
 */

import type { TypeDef } from "../types.js";

export const BASE_TYPES: Readonly<Record<string, TypeDef>> = {
  identifier: {
    nameHints: ["id", "key", "pk", "code", "sku", "number", "num", "record"],
    valueSignals: { min_unique_pct: 0.95 },
    suppress: ["cardinality", "pattern_consistency", "drift_detection"],
  },
  person_name: {
    nameHints: ["first_name", "last_name", "full_name", "person_name", "customer_name", "patient_name", "employee_name", "contact_name"],
    valueSignals: { mixed_case: true },
    suppress: ["pattern_consistency", "cardinality"],
  },
  email: {
    nameHints: ["email", "mail", "e_mail"],
    valueSignals: { format_match: "email", min_match_pct: 0.70 },
    suppress: ["pattern_consistency"],
  },
  phone: {
    nameHints: ["phone", "tel", "fax", "mobile", "cell"],
    valueSignals: { format_match: "phone", min_match_pct: 0.70 },
    suppress: ["type_inference", "pattern_consistency"],
  },
  address: {
    nameHints: ["address", "street", "addr", "line1", "line2"],
    valueSignals: { avg_length_min: 15 },
    suppress: ["pattern_consistency", "cardinality"],
  },
  free_text: {
    nameHints: ["notes", "comment", "description", "text", "memo", "message", "remarks"],
    valueSignals: { avg_length_min: 30, min_unique_pct: 0.80 },
    suppress: ["pattern_consistency", "cardinality", "type_inference", "drift_detection"],
  },
  datetime: {
    nameHints: ["date", "time", "created", "updated", "_at", "timestamp"],
    valueSignals: { format_match: "date" },
    suppress: ["pattern_consistency", "drift_detection"],
  },
  boolean: {
    nameHints: ["is_", "has_", "flag", "active", "enabled", "disabled"],
    valueSignals: { max_unique: 3 },
    suppress: ["range_distribution", "uniqueness"],
  },
  currency: {
    nameHints: ["amount", "price", "cost", "total", "fee", "payment", "charge", "balance"],
    valueSignals: { numeric: true },
    suppress: ["pattern_consistency"],
  },
  code_enum: {
    nameHints: ["status", "type", "category", "level", "tier", "grade", "rating", "priority"],
    valueSignals: { max_unique: 20 },
    suppress: ["uniqueness", "range_distribution"],
  },
  geo: {
    nameHints: ["country", "state", "city", "zip", "postal", "region", "province"],
    valueSignals: { short_strings: true },
    suppress: ["pattern_consistency"],
  },
};
