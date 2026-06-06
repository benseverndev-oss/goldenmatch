/**
 * config-edits.ts -- shared ConfigEdit lever vocabulary.
 *
 * Port of Python `goldenmatch/core/config_edits.py`: a small, closed edit
 * language where each edit is a validated transform of a `GoldenMatchConfig`
 * with a human label. `apply()` returns `null` for a no-op or an edit that
 * would produce an invalid config (the caller skips it).
 *
 * TS configs are immutable, so edits build new objects instead of pydantic's
 * deep-copy-then-mutate; pydantic revalidation maps to explicit checks
 * (`VALID_SCORERS`, the blocking-strategy union). Parity is fixture-verified:
 * tests/parity/config-edits.test.ts replays Python-emitted edit specs and
 * compares semantic projections of the results.
 */
import type {
  BlockingConfig,
  BlockingKeyConfig,
  GoldenMatchConfig,
  MatchkeyConfig,
} from "./types.js";
import { getMatchkeys, VALID_SCORERS } from "./types.js";

const PERTURBABLE_TYPES = new Set<string>(["weighted", "probabilistic"]);

const VALID_BLOCKING_STRATEGIES = new Set<string>([
  "static",
  "adaptive",
  "sorted_neighborhood",
  "multi_pass",
  "ann",
  "canopy",
  "ann_pairs",
  "learned",
]);

export interface ConfigEdit {
  readonly label: string;
  apply(config: GoldenMatchConfig): GoldenMatchConfig | null;
}

function clamp(x: number, lo = 0.0, hi = 1.0): number {
  return Math.max(lo, Math.min(hi, x));
}

function thresholdOf(mk: MatchkeyConfig): number | undefined {
  return (mk as { threshold?: number }).threshold;
}

/** Write the matchkeys back to whichever property held them. */
function withMatchkeys(
  config: GoldenMatchConfig,
  mks: readonly MatchkeyConfig[],
): GoldenMatchConfig {
  if (config.matchkeys !== undefined) return { ...config, matchkeys: mks };
  return { ...config, matchSettings: mks };
}

function arrayEq(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/**
 * Pydantic-revalidation parity for blocking edits (schemas.py BlockingConfig
 * validator): static/adaptive require `keys` (or `sub_block_keys`);
 * multi_pass requires `keys` or `passes`. An edit that would leave the
 * blocking config invalid is skipped (returns null), same as Python.
 */
function blockingValid(b: BlockingConfig): boolean {
  const keys = b.keys ?? [];
  const subBlockKeys =
    (b as { subBlockKeys?: readonly unknown[] }).subBlockKeys ?? [];
  const passes = (b as { passes?: readonly unknown[] }).passes ?? [];
  if (
    (b.strategy === "static" || b.strategy === "adaptive") &&
    keys.length === 0 &&
    subBlockKeys.length === 0
  ) {
    return false;
  }
  if (b.strategy === "multi_pass" && keys.length === 0 && passes.length === 0) {
    return false;
  }
  return true;
}

/** Shift every perturbable matchkey threshold by `delta` (clamped). */
export class ThresholdShift implements ConfigEdit {
  constructor(readonly delta: number) {}

  get label(): string {
    if (this.delta === 0.0) return "baseline";
    const sign = this.delta >= 0 ? "+" : "";
    return `threshold${sign}${this.delta.toFixed(2)}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    const mks = getMatchkeys(config);
    const perturbable = mks.filter(
      (mk) => PERTURBABLE_TYPES.has(mk.type) && thresholdOf(mk) !== undefined,
    );
    if (perturbable.length === 0) {
      return this.delta === 0.0 ? config : null;
    }
    let changed = false;
    const next = mks.map((mk) => {
      const t = thresholdOf(mk);
      if (PERTURBABLE_TYPES.has(mk.type) && t !== undefined) {
        const nt = clamp(t + this.delta);
        if (nt !== t) {
          changed = true;
          return { ...mk, threshold: nt } as MatchkeyConfig;
        }
      }
      return mk;
    });
    if (this.delta === 0.0) return withMatchkeys(config, next);
    return changed ? withMatchkeys(config, next) : null;
  }
}

/** Swap one matchkey field's scorer. */
export class ScorerSwap implements ConfigEdit {
  constructor(
    readonly matchkey: string,
    readonly field: string,
    readonly scorer: string,
  ) {}

  get label(): string {
    return `scorer:${this.field}=${this.scorer}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    // Pydantic-revalidation parity: an unknown scorer is an invalid edit.
    if (!(VALID_SCORERS as ReadonlySet<string>).has(this.scorer)) return null;
    let changed = false;
    const next = getMatchkeys(config).map((mk) => {
      if (mk.name !== this.matchkey) return mk;
      let mkChanged = false;
      const fields = mk.fields.map((f) => {
        if (f.field === this.field && f.scorer !== this.scorer) {
          mkChanged = true;
          return { ...f, scorer: this.scorer };
        }
        return f;
      });
      if (!mkChanged) return mk;
      changed = true;
      return { ...mk, fields } as MatchkeyConfig;
    });
    return changed ? withMatchkeys(config, next) : null;
  }
}

/** Change the blocking strategy (keys preserved). */
export class BlockingStrategyEdit implements ConfigEdit {
  constructor(readonly strategy: string) {}

  get label(): string {
    return `blocking:${this.strategy}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    if (
      config.blocking === undefined ||
      config.blocking.strategy === this.strategy ||
      !VALID_BLOCKING_STRATEGIES.has(this.strategy)
    ) {
      return null;
    }
    const blocking = {
      ...config.blocking,
      strategy: this.strategy as BlockingConfig["strategy"],
    };
    if (!blockingValid(blocking)) return null;
    return { ...config, blocking };
  }
}

/** Reweight one field of a weighted matchkey (floor 0.0). */
export class WeightShift implements ConfigEdit {
  constructor(
    readonly matchkey: string,
    readonly field: string,
    readonly delta: number,
  ) {}

  get label(): string {
    const sign = this.delta >= 0 ? "+" : "";
    return `weight:${this.field}${sign}${this.delta.toFixed(2)}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    let changed = false;
    const next = getMatchkeys(config).map((mk) => {
      if (mk.name !== this.matchkey || mk.type !== "weighted") return mk;
      let mkChanged = false;
      const fields = mk.fields.map((f) => {
        if (f.field === this.field && f.weight !== undefined) {
          const nw = Math.max(0.0, f.weight + this.delta);
          if (nw !== f.weight) {
            mkChanged = true;
            return { ...f, weight: nw };
          }
        }
        return f;
      });
      if (!mkChanged) return mk;
      changed = true;
      return { ...mk, fields } as MatchkeyConfig;
    });
    return changed ? withMatchkeys(config, next) : null;
  }
}

/**
 * Swap a matchkey between `weighted` and `probabilistic`. Going to weighted we
 * backfill a threshold (`linkThreshold` else 0.5) and uniform per-field
 * weights so the weighted invariant holds.
 */
export class MatchkeyTypeSwap implements ConfigEdit {
  constructor(
    readonly matchkey: string,
    readonly targetType: string,
  ) {}

  get label(): string {
    return `mktype:${this.matchkey}=${this.targetType}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    if (!PERTURBABLE_TYPES.has(this.targetType)) return null;
    let changed = false;
    const next = getMatchkeys(config).map((mk) => {
      if (
        mk.name !== this.matchkey ||
        mk.type === this.targetType ||
        !PERTURBABLE_TYPES.has(mk.type)
      ) {
        return mk;
      }
      changed = true;
      if (this.targetType === "weighted") {
        const link = (mk as { linkThreshold?: number }).linkThreshold;
        const threshold = thresholdOf(mk) ?? link ?? 0.5;
        const fields = mk.fields.map((f) =>
          f.weight === undefined ? { ...f, weight: 1.0 } : f,
        );
        return { ...mk, type: "weighted", threshold, fields } as MatchkeyConfig;
      }
      return { ...mk, type: "probabilistic" } as MatchkeyConfig;
    });
    return changed ? withMatchkeys(config, next) : null;
  }
}

/** Add or remove a blocking key, identified by its field set + transforms. */
export class BlockingKeyEdit implements ConfigEdit {
  constructor(
    readonly action: string,
    readonly fields: readonly string[],
    readonly transforms: readonly string[] = [],
  ) {}

  get label(): string {
    return `block-${this.action}:${this.fields.join("+")}`;
  }

  apply(config: GoldenMatchConfig): GoldenMatchConfig | null {
    if (
      config.blocking === undefined ||
      (this.action !== "add" && this.action !== "remove") ||
      this.fields.length === 0
    ) {
      return null;
    }
    const matches = (k: BlockingKeyConfig): boolean =>
      arrayEq(k.fields, this.fields) && arrayEq(k.transforms ?? [], this.transforms);
    const keys = config.blocking.keys ?? [];
    const exists = keys.some(matches);
    let nextKeys: readonly BlockingKeyConfig[];
    if (this.action === "add") {
      if (exists) return null;
      nextKeys = [
        ...keys,
        { fields: [...this.fields], transforms: [...this.transforms] },
      ];
    } else {
      if (!exists) return null;
      nextKeys = keys.filter((k) => !matches(k));
    }
    const blocking = { ...config.blocking, keys: nextKeys };
    if (!blockingValid(blocking)) return null;
    return { ...config, blocking };
  }
}

/**
 * Map one LLM-emitted edit spec (a JSON object) to a `ConfigEdit`. Returns
 * `null` for an unknown op or a malformed spec -- the closed vocabulary is the
 * only thing the LLM can drive, so junk is dropped, not run.
 */
export function editFromSpec(spec: unknown): ConfigEdit | null {
  if (typeof spec !== "object" || spec === null || Array.isArray(spec)) {
    return null;
  }
  const s = spec as Record<string, unknown>;
  const op = s["op"];
  try {
    if (op === "threshold_shift") return new ThresholdShift(asNum(s["delta"]));
    if (op === "scorer_swap") {
      return new ScorerSwap(asStr(s["matchkey"]), asStr(s["field"]), asStr(s["scorer"]));
    }
    if (op === "blocking_strategy") return new BlockingStrategyEdit(asStr(s["strategy"]));
    if (op === "weight_shift") {
      return new WeightShift(asStr(s["matchkey"]), asStr(s["field"]), asNum(s["delta"]));
    }
    if (op === "matchkey_type") {
      return new MatchkeyTypeSwap(asStr(s["matchkey"]), asStr(s["target_type"]));
    }
    if (op === "blocking_key") {
      const fields = s["fields"];
      if (!Array.isArray(fields)) return null;
      const transforms = Array.isArray(s["transforms"]) ? s["transforms"] : [];
      return new BlockingKeyEdit(
        asStr(s["action"]),
        fields.map(String),
        transforms.map(String),
      );
    }
  } catch {
    return null;
  }
  return null;
}

function asStr(v: unknown): string {
  if (v === null || v === undefined) throw new Error("missing");
  return String(v);
}

function asNum(v: unknown): number {
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error("not a number");
  return n;
}

/**
 * Parse an LLM response into validated `ConfigEdit`s. `{"action": "stop"}` /
 * `{"action": "satisfied"}` (or any non-list `edits`) yields an empty list.
 */
export function parseLlmEdits(payload: unknown): ConfigEdit[] {
  if (typeof payload !== "object" || payload === null) return [];
  const p = payload as Record<string, unknown>;
  if (p["action"] === "stop" || p["action"] === "satisfied") return [];
  const raw = p["edits"];
  if (!Array.isArray(raw)) return [];
  return raw.map(editFromSpec).filter((e): e is ConfigEdit => e !== null);
}

/**
 * Apply `edits` in sequence onto `config`, skipping any that don't apply
 * (return `null`). Returns the folded config -- equal to the input when every
 * edit was a no-op.
 */
export function foldEdits(
  config: GoldenMatchConfig,
  edits: readonly ConfigEdit[],
): GoldenMatchConfig {
  let cur = config;
  for (const edit of edits) {
    const next = edit.apply(cur);
    if (next !== null) cur = next;
  }
  return cur;
}
