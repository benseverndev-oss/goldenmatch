// Domain alias dictionaries. Mirrors infermap/dictionaries/__init__.py.
//
// Each domain is a Record<canonical, aliases[]>. Load via `loadDomain(name)`
// or combine multiple with `mergeDomains(names)`.

import { DOMAIN as GENERIC } from "./generic.js";
import { DOMAIN as HEALTHCARE } from "./healthcare.js";
import { DOMAIN as FINANCE } from "./finance.js";
import { DOMAIN as ECOMMERCE } from "./ecommerce.js";

export class UnknownDomainError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnknownDomainError";
  }
}

const DOMAINS: Record<string, Record<string, readonly string[]>> = {
  generic: GENERIC,
  healthcare: HEALTHCARE,
  finance: FINANCE,
  ecommerce: ECOMMERCE,
};

export function availableDomains(): string[] {
  return Object.keys(DOMAINS).sort();
}

export function loadDomain(name: string): Record<string, string[]> {
  const d = DOMAINS[name];
  if (!d) {
    throw new UnknownDomainError(
      `Unknown domain '${name}'. Available: ${JSON.stringify(availableDomains())}`
    );
  }
  // Copy into a mutable structure.
  const out: Record<string, string[]> = {};
  for (const [canonical, aliases] of Object.entries(d)) {
    out[canonical] = aliases.slice();
  }
  return out;
}

export function mergeDomains(names: readonly string[]): Record<string, string[]> {
  const merged: Record<string, string[]> = {};
  for (const name of names) {
    const domain = loadDomain(name);
    for (const [canonical, aliases] of Object.entries(domain)) {
      if (!merged[canonical]) {
        merged[canonical] = aliases.slice();
      } else {
        const existing = merged[canonical]!;
        for (const a of aliases) {
          if (!existing.includes(a)) existing.push(a);
        }
      }
    }
  }
  return merged;
}
