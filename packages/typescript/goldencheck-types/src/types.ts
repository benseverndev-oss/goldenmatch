// Canonical field-type definitions shared across the Golden Suite.

export interface FieldSpec {
  name_hints: string[];
  value_signals: Record<string, unknown>;
  suppress: string[];
  confidence_threshold?: number;
  description?: string;
}

export interface DomainPack {
  name: string;
  description: string;
  types: Record<string, FieldSpec>;
}

export interface FieldMapping {
  source_col: string;
  canonical: string | null;
  type: string; // canonical type name or "unknown"
  confidence: number;
  evidence: Record<string, unknown>; // InferMap-internal; do not depend on shape
}

export interface InferredSchema {
  domain: string;
  fields: Record<string, FieldMapping>;
  confidence: number;
}

export const isUnknown = (m: FieldMapping): boolean => m.type === "unknown";

export const unmappedCols = (s: InferredSchema): string[] =>
  Object.entries(s.fields)
    .filter(([, m]) => isUnknown(m))
    .map(([k]) => k);
