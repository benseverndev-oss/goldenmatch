"""`goldenmatch ingest-docs` — suggest a schema from a sample, then ingest documents to rows."""
from __future__ import annotations

import typer

from goldenmatch.documents import ingest_documents
from goldenmatch.documents.config import resolve_extractor
from goldenmatch.documents.schema_io import load_schema, save_schema
from goldenmatch.documents.suggest import suggest_schema_from_file

ingest_docs_app = typer.Typer(help="Ingest documents (PDF/image) into matchable records.")


@ingest_docs_app.command("suggest-schema")
def suggest_schema_cmd(
    sample: str = typer.Argument(..., help="A representative document (PDF/image)."),
    out: str = typer.Option(..., "--out", "-o", help="Write the proposed schema JSON here."),
    backend: str = typer.Option("vlm", help="Extraction backend."),
    model: str = typer.Option("gpt-4o", help="Vision model."),
):
    try:
        schema = suggest_schema_from_file(sample, backend=backend, model=model)
    except Exception as e:
        typer.echo(f"schema suggestion failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    save_schema(schema, out)
    typer.echo(f"Wrote {len(schema.fields)}-field schema to {out} -- review before running.", err=True)


@ingest_docs_app.command("run")
def run_cmd(
    docs: list[str] = typer.Argument(..., help="Document paths (PDF/image)."),
    schema: str = typer.Option(..., "--schema", "-s", help="Target schema JSON file."),
    out: str = typer.Option(..., "--out", "-o", help="Write records here (.csv or .parquet)."),
    backend: str = typer.Option("vlm", help="Extraction backend."),
    model: str = typer.Option("gpt-4o", help="Vision model."),
):
    target = load_schema(schema)
    extractor = resolve_extractor(backend, model)
    df, report = ingest_documents(docs, target, extractor=extractor, return_report=True)
    if str(out).endswith(".parquet"):
        df.write_parquet(out)
    else:
        df.write_csv(out)
    typer.echo(f"{report.n_rows} rows from {report.n_files} docs -> {out}", err=True)
    for f, err in report.errors:
        typer.echo(f"  skipped {f}: {err}", err=True)
