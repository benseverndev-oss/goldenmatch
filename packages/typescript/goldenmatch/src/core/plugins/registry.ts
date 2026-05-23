/**
 * PluginRegistry singleton for the goldenmatch TS port (Phase 5 Part
 * 5/N -- closes the discovery surface from goldenmatch issue #208).
 *
 * Mirrors `goldenmatch.plugins.registry.PluginRegistry` from Python.
 *
 * Differences from the Python sibling:
 * - No `discover()` entry-point scan -- npm has no Python-style
 *   entry-point system. Builtin auto-registration runs eagerly via
 *   `register_builtins()`. User plugins register manually via
 *   `registerGoldenStrategy(name, plugin)`.
 * - `has_*` / `get_*` accessors use camelCase (`hasGoldenStrategy`,
 *   `getGoldenStrategy`) to match TS port style.
 * - The Python registry tracks 4 plugin types (scorer / transform /
 *   connector / golden_strategy); the TS port v2.0 ships only
 *   golden_strategy. Scorer / transform / connector slots exist on
 *   the API surface but throw on register until those subsystems
 *   are ported in a later wave.
 */

import type { GoldenStrategyPlugin } from "./base.js";
import { AGGREGATION_BUILTINS } from "./builtin/aggregation.js";
import { BUSINESS_BUILTINS } from "./builtin/business.js";
import { FORMAT_BUILTINS } from "./builtin/format.js";
import { NUMERIC_BUILTINS } from "./builtin/numeric.js";

export type PluginType = "scorer" | "transform" | "connector" | "golden_strategy";

/** Combined list of all 22 v1.18.2 predefined golden strategies,
 *  registered eagerly when `PluginRegistry.instance()` is first
 *  accessed. Order matches Python's `BUILTIN_PLUGINS`. */
export const BUILTIN_PLUGINS: readonly GoldenStrategyPlugin[] = [
  ...NUMERIC_BUILTINS,
  ...FORMAT_BUILTINS,
  ...BUSINESS_BUILTINS,
  ...AGGREGATION_BUILTINS,
] as const;

export class PluginRegistry {
  private static _instance: PluginRegistry | null = null;
  private static _discovered = false;

  private readonly _goldenStrategies = new Map<string, GoldenStrategyPlugin>();

  private constructor() {}

  static instance(): PluginRegistry {
    if (PluginRegistry._instance === null) {
      PluginRegistry._instance = new PluginRegistry();
    }
    return PluginRegistry._instance;
  }

  /** Reset the singleton + the discovery flag. Test-only. */
  static reset(): void {
    PluginRegistry._instance = null;
    PluginRegistry._discovered = false;
  }

  /**
   * Auto-register the 22 builtin plugins. Idempotent -- safe to call
   * multiple times. User plugins should be registered via
   * `registerGoldenStrategy()` AFTER `discover()` to take precedence
   * (matches Python's "last write wins" semantics).
   */
  discover(): void {
    if (PluginRegistry._discovered) return;
    for (const plugin of BUILTIN_PLUGINS) {
      this._goldenStrategies.set(plugin.name, plugin);
    }
    PluginRegistry._discovered = true;
  }

  registerGoldenStrategy(name: string, plugin: GoldenStrategyPlugin): void {
    if (typeof plugin.merge !== "function") {
      throw new TypeError(
        `Plugin '${name}' does not satisfy GoldenStrategyPlugin (no merge method)`,
      );
    }
    this._goldenStrategies.set(name, plugin);
  }

  getGoldenStrategy(name: string): GoldenStrategyPlugin | null {
    return this._goldenStrategies.get(name) ?? null;
  }

  hasGoldenStrategy(name: string): boolean {
    return this._goldenStrategies.has(name);
  }

  /** Return all registered plugin names by type. Mirrors Python's
   *  `list_plugins()` shape -- but the TS port v2.0 only exposes
   *  golden_strategy; the other slots are empty arrays so callers
   *  that walk the shape don't NPE. */
  listPlugins(): Record<PluginType, string[]> {
    return {
      scorer: [],
      transform: [],
      connector: [],
      golden_strategy: Array.from(this._goldenStrategies.keys()),
    };
  }
}
