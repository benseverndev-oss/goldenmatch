import type { TypeDef } from "../../types.js";

export const FINANCE_TYPES: Readonly<Record<string, TypeDef>> = {
  account_number: { nameHints: ["account_number", "acct_num", "account_id", "acct_id"], valueSignals: { min_unique_pct: 0.90 }, suppress: ["cardinality", "pattern_consistency", "drift_detection"] },
  routing_number: { nameHints: ["routing", "aba", "swift", "bic"], valueSignals: { short_strings: true }, suppress: ["uniqueness", "pattern_consistency"] },
  cusip: { nameHints: ["cusip", "isin", "sedol", "ticker", "symbol"], valueSignals: { short_strings: true }, suppress: ["type_inference", "cardinality"] },
  currency_code: { nameHints: ["currency", "ccy", "currency_code"], valueSignals: { max_unique: 20 }, suppress: ["uniqueness", "range_distribution"] },
  transaction_type: { nameHints: ["transaction_type", "txn_type", "tx_type", "payment_method"], valueSignals: { max_unique: 20 }, suppress: ["uniqueness", "range_distribution"] },
  merchant: { nameHints: ["merchant", "vendor", "payee", "counterparty"], valueSignals: { mixed_case: true }, suppress: ["pattern_consistency"] },
  transaction_amount: { nameHints: ["transaction_amount", "txn_amount", "debit", "credit"], valueSignals: { numeric: true }, suppress: ["pattern_consistency"] },
  reference_number: { nameHints: ["reference", "ref_number", "confirmation", "trace"], valueSignals: { min_unique_pct: 0.95 }, suppress: ["cardinality", "pattern_consistency", "drift_detection"] },
};
