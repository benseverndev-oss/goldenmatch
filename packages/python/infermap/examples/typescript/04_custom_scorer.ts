// 04 — Define a custom scorer with domain-specific heuristics.
//
// Run: npx tsx examples/typescript/04_custom_scorer.ts
//
// Sometimes the defaults aren't enough. Here we add a scorer that recognizes
// "semantic prefix" matches specific to our data model — e.g. "usr_email"
// and "user_mail" share the "user/usr" prefix plus an "email/mail" suffix.

import {
  MapEngine,
  defaultScorers,
  defineScorer,
  makeScorerResult,
} from "infermap";

const PREFIX_SYNONYMS: Record<string, string> = {
  usr: "user",
  cust: "customer",
  cstmr: "customer",
  emp: "employee",
  empl: "employee",
  prod: "product",
  prd: "product",
};

const SUFFIX_SYNONYMS: Record<string, string> = {
  nm: "name",
  addr: "address",
  num: "number",
  id: "identifier",
  dt: "date",
  ts: "timestamp",
};

function normalizeParts(name: string): string[] {
  return name
    .toLowerCase()
    .split(/[_\- ]+/)
    .map((p) => PREFIX_SYNONYMS[p] ?? SUFFIX_SYNONYMS[p] ?? p);
}

const prefixSuffixScorer = defineScorer(
  "PrefixSuffixScorer",
  (source, target) => {
    const srcParts = normalizeParts(source.name);
    const tgtParts = normalizeParts(target.name);
    if (srcParts.length === 0 || tgtParts.length === 0) return null;

    // Jaccard similarity over normalized parts
    const srcSet = new Set(srcParts);
    const tgtSet = new Set(tgtParts);
    const intersection = new Set([...srcSet].filter((x) => tgtSet.has(x)));
    const union = new Set([...srcSet, ...tgtSet]);
    if (union.size === 0) return null;

    const score = intersection.size / union.size;
    if (score === 0) return null;
    return makeScorerResult(
      score,
      `PrefixSuffix: ${[...intersection].join("+")} (${intersection.size}/${union.size})`
    );
  },
  0.7 // weight
);

const engine = new MapEngine({
  scorers: [...defaultScorers(), prefixSuffixScorer],
});

const source = {
  fields: [
    { name: "usr_nm", dtype: "string" as const, sampleValues: ["alice"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
    { name: "cust_addr", dtype: "string" as const, sampleValues: ["123 Main"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
    { name: "order_dt", dtype: "string" as const, sampleValues: ["2026-04-08"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
  ],
  sourceName: "abbreviated",
  requiredFields: [],
};

const target = {
  fields: [
    { name: "user_name", dtype: "string" as const, sampleValues: ["zoe"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
    { name: "customer_address", dtype: "string" as const, sampleValues: ["456 Oak"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
    { name: "order_date", dtype: "string" as const, sampleValues: ["2026-05-01"], nullRate: 0, uniqueRate: 1, valueCount: 1, metadata: {} },
  ],
  sourceName: "canonical",
  requiredFields: [],
};

const result = engine.mapSchemas(source, target);
for (const m of result.mappings) {
  console.log(`${m.source.padEnd(14)} → ${m.target.padEnd(20)} ${m.confidence.toFixed(3)}`);
  console.log(`  reasoning: ${m.reasoning.slice(0, 120)}...`);
}
