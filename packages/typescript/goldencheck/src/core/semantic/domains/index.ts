import type { TypeDef } from "../../types.js";
import { HEALTHCARE_TYPES } from "./healthcare.js";
import { FINANCE_TYPES } from "./finance.js";
import { ECOMMERCE_TYPES } from "./ecommerce.js";

export { HEALTHCARE_TYPES } from "./healthcare.js";
export { FINANCE_TYPES } from "./finance.js";
export { ECOMMERCE_TYPES } from "./ecommerce.js";

export const DOMAIN_REGISTRY: Readonly<Record<string, Readonly<Record<string, TypeDef>>>> = {
  healthcare: HEALTHCARE_TYPES,
  finance: FINANCE_TYPES,
  ecommerce: ECOMMERCE_TYPES,
};

export function listAvailableDomains(): string[] {
  return Object.keys(DOMAIN_REGISTRY);
}

export function getDomainTypes(domain: string): Readonly<Record<string, TypeDef>> | undefined {
  return DOMAIN_REGISTRY[domain];
}
