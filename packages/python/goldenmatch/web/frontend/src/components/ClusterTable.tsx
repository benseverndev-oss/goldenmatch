import { useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import type { ClusterSummary } from "../lib/types";

type Props = {
  clusters: ClusterSummary[];
  onSelect: (id: number) => void;
  selectedId?: number;
};

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : n.toFixed(3);

const columns: ColumnDef<ClusterSummary>[] = [
  { accessorKey: "cluster_id", header: "Cluster" },
  { accessorKey: "size", header: "Size" },
  {
    accessorKey: "max_score",
    header: "Max",
    cell: (ctx) => fmt(ctx.getValue<number | null>()),
  },
  {
    accessorKey: "min_score",
    header: "Min",
    cell: (ctx) => fmt(ctx.getValue<number | null>()),
  },
  { accessorKey: "representative_row_id", header: "Rep row" },
];

export function ClusterTable({ clusters, onSelect, selectedId }: Props) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");

  const data = filter
    ? clusters.filter((c) => String(c.cluster_id).includes(filter))
    : clusters;

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="flex flex-col h-full">
      <div className="p-2 border-b">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by cluster id…"
          className="w-full px-2 py-1 text-sm border rounded"
        />
      </div>
      <div className="overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 sticky top-0">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    className="px-3 py-2 text-left font-medium cursor-pointer select-none border-b"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ▲", desc: " ▼" }[
                      h.column.getIsSorted() as string
                    ] ?? null}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const isSelected = row.original.cluster_id === selectedId;
              return (
                <tr
                  key={row.id}
                  onClick={() => onSelect(row.original.cluster_id)}
                  className={
                    "cursor-pointer border-b hover:bg-gray-50 " +
                    (isSelected ? "bg-blue-50" : "")
                  }
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2">
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
