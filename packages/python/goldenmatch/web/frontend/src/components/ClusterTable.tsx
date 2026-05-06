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

const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toFixed(3));

/** Renders cluster size as up to 8 dots; overflow as "+N". A glance carries the
 *  same information as the number, faster, and gives the table its character. */
function SizeGlyph({ size }: { size: number }) {
  const dots = Math.min(size, 8);
  const overflow = size - dots;
  return (
    <span className="inline-flex items-center gap-0.5 align-middle">
      {Array.from({ length: dots }).map((_, i) => (
        <span
          key={i}
          className="block h-1.5 w-1.5 rounded-full bg-gold-300"
          style={{ opacity: 0.55 + (i / Math.max(dots, 1)) * 0.45 }}
        />
      ))}
      {overflow > 0 && (
        <span className="num text-[11px] text-ink-400 ml-1">+{overflow}</span>
      )}
    </span>
  );
}

function ScoreCell({ value }: { value: number | null }) {
  if (value == null) return <span className="text-ink-500">—</span>;
  return <span className="num text-ink-100 tabular-nums">{fmt(value)}</span>;
}

const columns: ColumnDef<ClusterSummary>[] = [
  {
    accessorKey: "cluster_id",
    header: "Cluster",
    cell: (ctx) => (
      <span className="num text-ink-100 tabular-nums">
        {ctx.getValue<number>()}
      </span>
    ),
  },
  {
    accessorKey: "size",
    header: "Size",
    cell: (ctx) => (
      <span className="flex items-center gap-2">
        <span className="num text-ink-200 tabular-nums w-4 text-right">
          {ctx.getValue<number>()}
        </span>
        <SizeGlyph size={ctx.getValue<number>()} />
      </span>
    ),
  },
  {
    accessorKey: "max_score",
    header: "Max",
    cell: (ctx) => <ScoreCell value={ctx.getValue<number | null>()} />,
  },
  {
    accessorKey: "min_score",
    header: "Min",
    cell: (ctx) => <ScoreCell value={ctx.getValue<number | null>()} />,
  },
  {
    accessorKey: "representative_row_id",
    header: "Rep",
    cell: (ctx) => (
      <span className="num text-ink-400 tabular-nums">
        #{ctx.getValue<number>()}
      </span>
    ),
  },
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
      <div className="px-3 py-2 border-b border-ink-800 flex items-center gap-3">
        <span className="eyebrow">filter</span>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="cluster id…"
          className="flex-1 !py-0.5"
        />
        <span className="num text-[11px] text-ink-500 tabular-nums">
          {data.length}/{clusters.length}
        </span>
      </div>
      <div className="overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-ink-900/95 backdrop-blur z-[1]">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const sorted = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      onClick={h.column.getToggleSortingHandler()}
                      className="px-3 py-2 text-left eyebrow cursor-pointer select-none border-b border-ink-800 hover:text-gold-200 transition-colors"
                    >
                      <span className="inline-flex items-center gap-1">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span className="text-gold-300 w-2 inline-block">
                          {sorted === "asc"
                            ? "▲"
                            : sorted === "desc"
                              ? "▼"
                              : ""}
                        </span>
                      </span>
                    </th>
                  );
                })}
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
                    "cursor-pointer border-b border-ink-800/60 transition-colors " +
                    (isSelected
                      ? "bg-ink-800/80 gold-edge"
                      : "hover:bg-ink-800/40")
                  }
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2.5 align-middle">
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {data.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-8 text-center text-sm text-ink-500"
                >
                  No clusters match{filter ? ` "${filter}"` : ""}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
