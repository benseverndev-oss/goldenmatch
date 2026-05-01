// DomainPackTarget — adapts a goldencheck-types DomainPack as an InferMap target.
import type { DomainPack } from "goldencheck-types";
import type { SchemaInfo } from "./types.js";

export class DomainPackTarget {
  constructor(public readonly pack: DomainPack) {}

  toSchemaInfo(): SchemaInfo {
    return {
      sourceName: `domain:${this.pack.name}`,
      fields: Object.entries(this.pack.types).map(([type_name, spec]) => ({
        name: type_name,
        dtype: "string",
        sampleValues: [...spec.name_hints],
        nullRate: 0,
        uniqueRate: 0,
        valueCount: 0,
        metadata: {
          value_signals: spec.value_signals,
          confidence_threshold: spec.confidence_threshold,
          domain: this.pack.name,
        },
        canonicalName: null,
      })) as any,
      requiredFields: [],
    } as any;
  }
}

export function isDomainPackTarget(x: unknown): x is DomainPackTarget {
  return x instanceof DomainPackTarget;
}
