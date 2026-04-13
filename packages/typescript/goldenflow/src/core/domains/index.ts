import type { DomainPack } from "../types.js";

const DOMAIN_LOADERS: Readonly<Record<string, () => Promise<{ PACK: DomainPack }>>> = {
  people_hr: () => import("./people-hr.js"),
  healthcare: () => import("./healthcare.js"),
  finance: () => import("./finance.js"),
  ecommerce: () => import("./ecommerce.js"),
  real_estate: () => import("./real-estate.js"),
};

export async function loadDomain(name: string): Promise<DomainPack | null> {
  const key = name.toLowerCase().replace(/-/g, "_").replace(/\//g, "_");
  const loader = DOMAIN_LOADERS[key];
  if (!loader) return null;
  const mod = await loader();
  return mod.PACK;
}

export function listDomains(): string[] {
  return Object.keys(DOMAIN_LOADERS);
}
