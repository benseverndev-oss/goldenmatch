import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api } from "../lib/api";
import type { Project } from "../lib/types";

export function Home() {
  const { data, isLoading, error } = useQuery<Project>({
    queryKey: ["project"],
    queryFn: api.project,
  });
  if (isLoading) return <div className="p-8">Loading…</div>;
  if (error) return <div className="p-8 text-red-600">{String(error)}</div>;
  if (!data) return null;
  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold">{data.project_root}</h1>
      <p className="text-sm text-gray-500">
        {data.config_path ?? "(no config)"}
      </p>
      <h2 className="mt-6 text-lg">Runs</h2>
      <ul className="mt-2 space-y-2">
        {data.runs.map((r) => (
          <li key={r.run_name} className="border rounded p-3">
            <Link
              to="/runs/$name"
              params={{ name: r.run_name }}
              className="font-mono"
            >
              {r.run_name}
            </Link>
            <span className="ml-3 text-sm text-gray-600">
              {r.cluster_count} clusters · {r.row_count} rows ·{" "}
              {r.total_pairs} pairs
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
