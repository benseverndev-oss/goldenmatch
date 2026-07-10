import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { IngestResult, TargetSchema } from "../lib/api";
import { SchemaEditor } from "../components/SchemaEditor";
import { DocumentResults } from "../components/DocumentResults";
import { humanizeError } from "../lib/errors";

export function Documents() {
  const [files, setFiles] = useState<File[]>([]);
  const [schema, setSchema] = useState<TargetSchema | null>(null);

  const suggestMut = useMutation<{ schema: TargetSchema }, Error, void>({
    mutationFn: () => api.suggestSchema(files[0]!),
    onSuccess: (data) => setSchema(data.schema),
  });

  const ingestMut = useMutation<IngestResult, Error, void>({
    mutationFn: () => api.ingestDocuments(files, schema!),
  });

  return (
    <div className="px-8 py-10 max-w-6xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">documents</p>
        <h1 className="display text-3xl text-ink-900">Ingest</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          Upload PDFs or images, let the model suggest a schema, review/edit
          it, then extract records into a table you can download as CSV.
        </p>
      </header>

      <section className="card px-5 py-4 mb-8">
        <label className="block">
          <span className="eyebrow block mb-1">upload files</span>
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            aria-label="upload files"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            className="w-full"
          />
        </label>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            disabled={files.length === 0 || suggestMut.isPending}
            onClick={() => suggestMut.mutate()}
          >
            {suggestMut.isPending ? "Suggesting…" : "Suggest schema"}
          </button>
          {files.length > 0 && (
            <span className="text-xs text-ink-500">
              {files.length} file{files.length === 1 ? "" : "s"} selected
            </span>
          )}
        </div>
        {suggestMut.error && (
          <p className="mt-3 text-xs text-red-700 font-mono break-all">
            ↳ {humanizeError(suggestMut.error.message)}
          </p>
        )}
      </section>

      {schema && (
        <section className="mb-8">
          <SchemaEditor
            schema={schema}
            onChange={(next) => {
              setSchema(next);
              ingestMut.reset();
            }}
          />
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              className="btn btn-primary"
              disabled={
                files.length === 0 || schema.fields.length === 0 || ingestMut.isPending
              }
              onClick={() => ingestMut.mutate()}
            >
              {ingestMut.isPending ? "Extracting…" : "Extract"}
            </button>
          </div>
          {ingestMut.error && (
            <p className="mt-3 text-xs text-red-700 font-mono break-all">
              ↳ {humanizeError(ingestMut.error.message)}
            </p>
          )}
        </section>
      )}

      {ingestMut.data && schema && (
        <DocumentResults
          result={ingestMut.data}
          schemaColumns={schema.fields.map((f) => f.name)}
        />
      )}
    </div>
  );
}
