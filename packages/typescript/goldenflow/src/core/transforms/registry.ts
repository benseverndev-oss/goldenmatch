/**
 * Transform registry — TS equivalent of goldenflow/transforms/__init__.py.
 * Transforms self-register via registerTransform().
 */

import type { TransformFunction, TransformInfo, TransformMode } from "../types.js";

const _REGISTRY = new Map<string, TransformInfo>();

export interface RegisterOptions {
  name: string;
  inputTypes: readonly string[];
  autoApply?: boolean;
  priority?: number;
  mode?: TransformMode;
}

export function registerTransform(opts: RegisterOptions, func: TransformFunction): void {
  _REGISTRY.set(opts.name, {
    name: opts.name,
    func,
    inputTypes: opts.inputTypes,
    autoApply: opts.autoApply ?? false,
    priority: opts.priority ?? 50,
    mode: opts.mode ?? "series",
  });
}

export function getTransform(name: string): TransformInfo | undefined {
  return _REGISTRY.get(name);
}

export function listTransforms(): TransformInfo[] {
  return [..._REGISTRY.values()].sort((a, b) => b.priority - a.priority);
}

export function parseTransformName(raw: string): [string, string[]] {
  const parts = raw.split(":");
  return [parts[0]!, parts.slice(1)];
}

export function registry(): ReadonlyMap<string, TransformInfo> {
  return _REGISTRY;
}
