import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import type { IngestResult } from "../lib/api";
import { recordsToCsv } from "../lib/documentsCsv";

type Props = {
  result: IngestResult;
  schemaColumns: string[];
};

function renderVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function downloadCsv(csv: string, filename: string) {
  if (typeof document === "undefined") return; // no-op under jsdom
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function DocumentResults({ result, schemaColumns }: Props) {
  const { records, report } = result;
  const tableColumns = [...schemaColumns, "_extract_confidence"];

  const columns: ColumnDef<Record<string, unknown>>[] = tableColumns.map((c) => ({
    accessorKey: c,
    header: c,
    cell: (ctx) => (
      <span className={c === "_extract_confidence" ? "num tabular-nums" : undefined}>
        {renderVal(ctx.getValue())}
      </span>
    ),
  }));

  const table = useReactTable({
    data: records,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const handleDownload = () => {
    const csvColumns = [...schemaColumns, "_source_file", "_source_page", "_extract_confidence"];
    const csv = recordsToCsv(records, csvColumns);
    downloadCsv(csv, "documents.csv");
  };

  return (
    <section className="space-y-4">
      <header className="flex items-baseline justify-between">
        <p className="text-sm text-ink-700">
          {report.n_rows} record{report.n_rows === 1 ? "" : "s"} from {report.n_files} file
          {report.n_files === 1 ? "" : "s"}
        </p>
        <button type="button" className="btn btn-primary" onClick={handleDownload}>
          Download CSV
        </button>
      </header>

      {report.errors.length > 0 && (
        <div className="card px-4 py-3 space-y-1">
          <p className="eyebrow text-red-700">errors · {report.errors.length}</p>
          <ul className="text-xs text-red-700 font-mono space-y-0.5">
            {report.errors.map((e, i) => (
              <li key={i}>
                {e.file}: {e.error}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="text-left eyebrow text-ink-500 border-b border-ink-200">
                {hg.headers.map((h) => (
                  <th key={h.id} className="py-2 pr-3">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-b border-ink-100 last:border-b-0">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="py-2 pr-3 text-ink-800">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {records.length === 0 && (
              <tr>
                <td colSpan={tableColumns.length} className="px-3 py-8 text-center text-sm text-ink-400">
                  No records extracted.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
