import { useParams, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { RunInspector } from "../components/RunInspector";

export function Inspector() {
  const { name } = useParams({ strict: false }) as { name: string };
  const manifest = useQuery({
    queryKey: ["run", name],
    queryFn: () => api.run(name),
  });

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      <header className="px-8 py-4 border-b border-ink-800 flex items-baseline justify-between gap-6">
        <div className="flex items-baseline gap-4 min-w-0">
          <Link
            to="/"
            className="eyebrow text-ink-500 hover:text-gold-200 transition-colors"
          >
            ← project
          </Link>
          <div className="min-w-0">
            <p className="eyebrow mb-0.5">run</p>
            <h1 className="display text-2xl text-gold-200 truncate">{name}</h1>
          </div>
        </div>
        {manifest.data && (
          <dl className="hidden md:flex items-baseline gap-7 shrink-0">
            <Stat label="rows" value={manifest.data.row_count} />
            <Stat label="clusters" value={manifest.data.cluster_count} />
            <Stat label="pairs" value={manifest.data.total_pairs} />
          </dl>
        )}
      </header>
      <div className="flex-1 min-h-0">
        <RunInspector name={name} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-right">
      <p className="eyebrow">{label}</p>
      <p className="num text-lg text-ink-100 tabular-nums">{value}</p>
    </div>
  );
}
