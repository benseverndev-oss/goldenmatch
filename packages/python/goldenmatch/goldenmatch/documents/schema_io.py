"""TargetSchema <-> JSON. Pure stdlib, offline. The serializable schema form used by the
MCP tools and the CLI."""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.documents.types import Field, TargetSchema


def schema_to_dict(schema: TargetSchema) -> dict:
    return {"fields": [{"name": f.name, "kind": f.kind, "hint": f.hint}
                       for f in schema.fields]}


def schema_from_dict(d: dict) -> TargetSchema:
    if not isinstance(d, dict) or "fields" not in d or not isinstance(d["fields"], list):
        raise ValueError("schema must be an object with a 'fields' list")
    fields = []
    for item in d["fields"]:
        if "name" not in item:
            raise ValueError(f"schema field missing 'name': {item!r}")
        fields.append(Field(name=item["name"], kind=item.get("kind", "text"),
                            hint=item.get("hint")))
    if not fields:
        raise ValueError("schema has no fields")
    return TargetSchema(fields)


def save_schema(schema: TargetSchema, path) -> None:
    Path(path).write_text(json.dumps(schema_to_dict(schema), indent=2), encoding="utf-8")


def load_schema(path) -> TargetSchema:
    return schema_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
