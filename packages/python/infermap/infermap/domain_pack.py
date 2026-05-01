"""DomainPackTarget — adapts a goldencheck-types DomainPack as an InferMap target."""
from __future__ import annotations

from goldencheck_types import DomainPack

from infermap.types import FieldInfo, SchemaInfo


class DomainPackTarget:
    """Wraps a goldencheck-types DomainPack as an InferMap target schema.

    Each canonical type in the pack becomes a target field. The type's
    ``name_hints`` populate ``sample_values`` so InferMap's existing
    ``FuzzyNameScorer`` can fire on column-name matches without code changes.
    """

    def __init__(self, pack: DomainPack):
        self.pack = pack

    def to_schema_info(self) -> SchemaInfo:
        fields = []
        for type_name, spec in self.pack.types.items():
            fields.append(
                FieldInfo(
                    name=type_name,
                    dtype="string",
                    sample_values=list(spec.name_hints),
                    metadata={
                        "value_signals": dict(spec.value_signals),
                        "confidence_threshold": spec.confidence_threshold,
                        "domain": self.pack.name,
                    },
                )
            )
        return SchemaInfo(
            fields=fields,
            source_name=f"domain:{self.pack.name}",
        )
