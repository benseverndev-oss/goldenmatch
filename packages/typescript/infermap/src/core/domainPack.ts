// DomainPackTarget — adapts a goldencheck-types DomainPack as an InferMap target.
import type { DomainPack } from "goldencheck-types";
import type { FieldInfo, SchemaInfo } from "./types.js";

export class DomainPackTarget {
  constructor(public readonly pack: DomainPack) {}

  toSchemaInfo(): SchemaInfo {
    const fields: FieldInfo[] = Object.entries(this.pack.types).map(
      ([type_name, spec]) => ({
        name: type_name,
        dtype: "string",
        sampleValues: [...spec.name_hints],
        nullRate: 0,
        uniqueRate: 0,
        valueCount: 0,
        metadata: {
          value_signals: spec.value_signals,
          confidence_threshold: spec.confidence_threshold,
          // Forward suppress + description so downstream scanners can read
          // them without re-loading the YAML pack. (Same shape as the
          // Python `DomainPackTarget.to_schema_info`.)
          suppress: [...spec.suppress],
          description: spec.description,
          domain: this.pack.name,
        },
      }),
    );
    return {
      sourceName: `domain:${this.pack.name}`,
      fields,
      requiredFields: [],
    };
  }
}

export function isDomainPackTarget(x: unknown): x is DomainPackTarget {
  return x instanceof DomainPackTarget;
}
