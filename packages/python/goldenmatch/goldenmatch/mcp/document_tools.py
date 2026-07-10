"""MCP tools for document/image ingest. Handlers return JSON-serializable dicts, matching
handle_routing_tool so both the stdio call_tool and aggregator dispatch paths work."""
from __future__ import annotations

from mcp.types import Tool

from goldenmatch.documents import ingest_documents
from goldenmatch.documents.config import resolve_extractor  # noqa: F401 (monkeypatch seam)
from goldenmatch.documents.schema_io import schema_from_dict, schema_to_dict
from goldenmatch.documents.suggest import suggest_schema_from_file  # noqa: F401 (seam)

DOCUMENT_TOOLS = [
    Tool(
        name="documents_suggest_schema",
        description="Propose a target extraction schema (JSON) from a sample document image/PDF.",
        inputSchema={
            "type": "object",
            "properties": {
                "sample_path": {"type": "string"},
                "backend": {"type": "string", "default": "vlm"},
                "model": {"type": "string", "default": "gpt-4o"},
            },
            "required": ["sample_path"],
        },
    ),
    Tool(
        name="documents_ingest",
        description=("Extract records from documents (PDF/image) against a target schema into "
                     "rows ready for dedupe_df. Returns records + an ingest report."),
        inputSchema={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "schema": {"type": "object", "description": "schema JSON: {'fields':[...]}"},
                "backend": {"type": "string", "default": "vlm"},
                "model": {"type": "string", "default": "gpt-4o"},
                "drop_empty": {"type": "boolean", "default": True},
                "out_path": {"type": "string", "description": "optional CSV/parquet to also write"},
            },
            "required": ["paths", "schema"],
        },
    ),
]
DOCUMENT_TOOL_NAMES = frozenset(t.name for t in DOCUMENT_TOOLS)


def handle_document_tool(name: str, arguments: dict) -> dict:
    if name == "documents_suggest_schema":
        schema = suggest_schema_from_file(
            arguments["sample_path"],
            backend=arguments.get("backend", "vlm"),
            model=arguments.get("model", "gpt-4o"))
        return {"schema": schema_to_dict(schema)}

    if name == "documents_ingest":
        schema = schema_from_dict(arguments["schema"])
        extractor = resolve_extractor(arguments.get("backend", "vlm"),
                                      arguments.get("model", "gpt-4o"))
        df, report = ingest_documents(
            arguments["paths"], schema, extractor=extractor,
            drop_empty=arguments.get("drop_empty", True), return_report=True)
        out_path = arguments.get("out_path")
        if out_path:
            (df.write_parquet if str(out_path).endswith(".parquet") else df.write_csv)(out_path)
        return {
            "records": df.to_dicts(),
            "report": {"n_files": report.n_files, "n_rows": report.n_rows,
                       "errors": [{"file": f, "error": e} for (f, e) in report.errors]},
            **({"out_path": str(out_path)} if out_path else {}),
        }
    raise ValueError(f"unknown document tool: {name}")
