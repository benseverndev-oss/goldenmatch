/**
 * Business-shaped golden strategies (Phase 5 Part 3 of N -- closes
 * the next 6 of 22 plugins from goldenmatch issue #208).
 *
 * Includes the date-handling pair (`freshness_with_max_age` +
 * `weighted_by_recency`) -- their parity-test fixtures use dates
 * with stable relative ordering so the winner is deterministic
 * regardless of when the test runs.
 */

import type {
  GoldenStrategyMergeOpts,
  GoldenStrategyPlugin,
  GoldenStrategyResult,
} from "../base.js";

const DEFAULT_LIFECYCLE_ORDER: ReadonlyArray<string> = [
  "subscriber",
  "lead",
  "marketing_qualified_lead",
  "mql",
  "sales_qualified_lead",
  "sql",
  "opportunity",
  "customer",
  "evangelist",
];

function pyStr(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

function mostCommon<T>(items: ReadonlyArray<T>): { key: T; count: number } {
  const counts = new Map<T, number>();
  for (const item of items) counts.set(item, (counts.get(item) ?? 0) + 1);
  let bestKey: T | undefined;
  let bestCount = -1;
  for (const [key, count] of counts) {
    if (count > bestCount) {
      bestCount = count;
      bestKey = key;
    }
  }
  return { key: bestKey as T, count: bestCount };
}

function parseDate(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value : null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    // Python `datetime.fromtimestamp(float)` treats as seconds.
    return new Date(value * 1000);
  }
  if (typeof value === "boolean") return null;
  const s = String(value).trim();
  if (!s) return null;
  // Python's `datetime.fromisoformat` accepts most ISO 8601;
  // JS `Date.parse` handles the standard forms used in fixtures
  // ("2024-01-15", "2024-01-15T12:00:00Z", "2024-01-15T12:00:00+00:00").
  const ms = Date.parse(s);
  if (Number.isFinite(ms)) return new Date(ms);
  // Python fallbacks (`%Y/%m/%d`, `%m/%d/%Y`, etc.) -- handle the
  // unambiguous slash form. The ambiguous d/m/Y vs m/d/Y case is
  // not exercised in fixtures (we always use ISO).
  const slashMatch = s.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (slashMatch) {
    return new Date(`${slashMatch[1]}-${slashMatch[2]}-${slashMatch[3]}T00:00:00Z`);
  }
  return null;
}

export class SystemOfRecordStrategy implements GoldenStrategyPlugin {
  readonly name = "system_of_record";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const nonNull: Array<{ idx: number; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      if (values[i] !== null && values[i] !== undefined) {
        nonNull.push({ idx: i, raw: values[i] });
      }
    }
    if (nonNull.length === 0) return [null, 0.0] as const;
    const sources = opts.sources;
    const priority =
      (opts.ruleKwargs?.["source_priority"] as readonly string[] | undefined) ??
      [];
    if (!sources || priority.length === 0) {
      return [nonNull[0]!.raw, 0.5, nonNull[0]!.idx] as const;
    }
    for (let rank = 0; rank < priority.length; rank++) {
      const targetSrc = priority[rank];
      for (let i = 0; i < sources.length; i++) {
        if (sources[i] === targetSrc && values[i] !== null && values[i] !== undefined) {
          const conf = Math.max(0.5, 1.0 - rank * 0.1);
          return [values[i], conf, i] as const;
        }
      }
    }
    return [nonNull[0]!.raw, 0.4, nonNull[0]!.idx] as const;
  }
}

export class LifecycleStageStrategy implements GoldenStrategyPlugin {
  readonly name = "lifecycle_stage";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const nonNull: Array<{ idx: number; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      if (values[i] !== null && values[i] !== undefined) {
        nonNull.push({ idx: i, raw: values[i] });
      }
    }
    if (nonNull.length === 0) return [null, 0.0] as const;
    const order =
      (opts.ruleKwargs?.["lifecycle_order"] as readonly unknown[] | undefined) ??
      DEFAULT_LIFECYCLE_ORDER;
    const orderMap = new Map<string, number>();
    for (let i = 0; i < order.length; i++) {
      orderMap.set(pyStr(order[i]).trim().toLowerCase(), i);
    }
    const ranked = nonNull.map((x) => ({
      idx: x.idx,
      raw: x.raw,
      rank: orderMap.get(pyStr(x.raw).trim().toLowerCase()) ?? -1,
    }));
    let maxRank = ranked[0]!.rank;
    for (let i = 1; i < ranked.length; i++) {
      if (ranked[i]!.rank > maxRank) maxRank = ranked[i]!.rank;
    }
    const tied = ranked.filter((x) => x.rank === maxRank);
    const conf = tied.length === 1 ? 1.0 : 0.7;
    return [tied[0]!.raw, conf, tied[0]!.idx] as const;
  }
}

export class FreshnessWithMaxAgeStrategy implements GoldenStrategyPlugin {
  readonly name = "freshness_with_max_age";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const dates = opts.dates;
    if (!dates) return [null, 0.0] as const;
    const maxAgeDays =
      Number((opts.ruleKwargs?.["max_age_days"] as number | undefined) ?? 365);
    // Allow tests to pin `now` via ruleKwargs.now_iso for determinism.
    const nowIso = opts.ruleKwargs?.["now_iso"] as string | undefined;
    const now = nowIso ? new Date(nowIso) : new Date();
    const cutoffMs = now.getTime() - maxAgeDays * 86400 * 1000;
    const candidates: Array<{ idx: number; dt: Date; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v === null || v === undefined) continue;
      const dt = parseDate(dates[i]);
      if (dt === null || dt.getTime() < cutoffMs) continue;
      candidates.push({ idx: i, dt, raw: v });
    }
    if (candidates.length === 0) return [null, 0.0] as const;
    candidates.sort((a, b) => b.dt.getTime() - a.dt.getTime());
    const topMs = candidates[0]!.dt.getTime();
    const tied = candidates.filter((x) => x.dt.getTime() === topMs);
    const conf = tied.length === 1 ? 1.0 : 0.7;
    return [tied[0]!.raw, conf, tied[0]!.idx] as const;
  }
}

export class EnumCanonicalStrategy implements GoldenStrategyPlugin {
  readonly name = "enum_canonical";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const nonNull: Array<{ idx: number; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      if (values[i] !== null && values[i] !== undefined) {
        nonNull.push({ idx: i, raw: values[i] });
      }
    }
    if (nonNull.length === 0) return [null, 0.0] as const;
    const rawMap =
      (opts.ruleKwargs?.["alias_map"] as Record<string, unknown> | undefined) ??
      {};
    const aliasMap = new Map<string, unknown>();
    for (const [k, canonical] of Object.entries(rawMap)) {
      aliasMap.set(pyStr(k).trim().toLowerCase(), canonical);
    }
    const canonicalize = (v: unknown): unknown => {
      const key = pyStr(v).trim().toLowerCase();
      const mapped = aliasMap.get(key);
      return mapped !== undefined ? mapped : v;
    };
    const normalized = nonNull.map((x) => ({ idx: x.idx, c: canonicalize(x.raw) }));
    const { key: winner, count } = mostCommon(normalized.map((x) => x.c));
    const firstIdx = normalized.find(
      (x) => x.c === winner || JSON.stringify(x.c) === JSON.stringify(winner),
    )!.idx;
    return [winner, count / nonNull.length, firstIdx] as const;
  }
}

export class RegexValidatedStrategy implements GoldenStrategyPlugin {
  readonly name = "regex_validated";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const nonNull: Array<{ idx: number; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      if (values[i] !== null && values[i] !== undefined) {
        nonNull.push({ idx: i, raw: values[i] });
      }
    }
    if (nonNull.length === 0) return [null, 0.0] as const;
    const pattern = opts.ruleKwargs?.["pattern"] as string | undefined;
    const fallback =
      (opts.ruleKwargs?.["fallback"] as string | undefined) ?? "first_non_null";
    if (!pattern) {
      return [nonNull[0]!.raw, 0.5, nonNull[0]!.idx] as const;
    }
    let compiled: RegExp;
    try {
      // Python's `re.fullmatch` -> anchor both ends explicitly.
      compiled = new RegExp(`^(?:${pattern})$`);
    } catch {
      return [nonNull[0]!.raw, 0.3, nonNull[0]!.idx] as const;
    }
    const matched = nonNull.filter((x) => compiled.test(pyStr(x.raw)));
    if (matched.length > 0) {
      const { key: winner, count } = mostCommon(matched.map((x) => x.raw));
      const firstIdx = matched.find(
        (x) => x.raw === winner || JSON.stringify(x.raw) === JSON.stringify(winner),
      )!.idx;
      return [winner, count / matched.length, firstIdx] as const;
    }
    if (fallback === "null") return [null, 0.0] as const;
    return [nonNull[0]!.raw, 0.3, nonNull[0]!.idx] as const;
  }
}

export class WeightedByRecencyStrategy implements GoldenStrategyPlugin {
  readonly name = "weighted_by_recency";

  merge(
    values: ReadonlyArray<unknown>,
    opts: GoldenStrategyMergeOpts = {},
  ): GoldenStrategyResult {
    const dates = opts.dates;
    if (!dates) return [null, 0.0] as const;
    let halfLifeDays = Number(
      (opts.ruleKwargs?.["half_life_days"] as number | undefined) ?? 30,
    );
    if (!(halfLifeDays > 0)) halfLifeDays = 30;
    const nowIso = opts.ruleKwargs?.["now_iso"] as string | undefined;
    const now = nowIso ? new Date(nowIso) : new Date();
    const scored: Array<{ idx: number; dt: Date; weight: number; raw: unknown }> = [];
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v === null || v === undefined) continue;
      const dt = parseDate(dates[i]);
      if (dt === null) continue;
      const ageDays = (now.getTime() - dt.getTime()) / 86_400_000;
      const weight = Math.exp(-ageDays / halfLifeDays);
      scored.push({ idx: i, dt, weight, raw: v });
    }
    if (scored.length === 0) return [null, 0.0] as const;
    scored.sort((a, b) => b.weight - a.weight);
    const topWeight = scored[0]!.weight;
    const tied = scored.filter((x) => x.weight === topWeight);
    const conf = tied.length === 1 ? 1.0 : 0.7;
    return [tied[0]!.raw, conf, tied[0]!.idx] as const;
  }
}

export const BUSINESS_BUILTINS: readonly GoldenStrategyPlugin[] = [
  new SystemOfRecordStrategy(),
  new LifecycleStageStrategy(),
  new FreshnessWithMaxAgeStrategy(),
  new EnumCanonicalStrategy(),
  new RegexValidatedStrategy(),
  new WeightedByRecencyStrategy(),
] as const;
