import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  IdentityListResponse,
  IdentityStatsResponse,
  IdentitySummary,
  IdentityView,
} from "../lib/api";

export function Identities() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [datasetFilter, setDatasetFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const stats = useQuery<IdentityStatsResponse>({
    queryKey: ["identity-stats", datasetFilter],
    queryFn: () => api.identityStats(datasetFilter || undefined),
    retry: false,
  });

  const list = useQuery<IdentityListResponse>({
    queryKey: ["identity-list", datasetFilter, statusFilter],
    queryFn: () =>
      api.identityList({
        dataset: datasetFilter || undefined,
        status: statusFilter || undefined,
        limit: 100,
      }),
    retry: false,
  });

  const detail = useQuery<IdentityView>({
    queryKey: ["identity-detail", selected],
    queryFn: () => api.identityGet(selected!),
    enabled: !!selected,
  });

  const splitMut = useMutation({
    mutationFn: (vars: { eid: string; record_ids: string[] }) =>
      api.identitySplit(vars.eid, { record_ids: vars.record_ids, reason: "web" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["identity-list"] });
      qc.invalidateQueries({ queryKey: ["identity-detail"] });
      qc.invalidateQueries({ queryKey: ["identity-stats"] });
    },
  });

  if (list.error) {
    return (
      <div className="px-8 py-10 max-w-6xl mx-auto">
        <header className="mb-8">
          <p className="eyebrow mb-2">identities</p>
          <h1 className="display text-3xl text-ink-900">Identity graph</h1>
        </header>
        <div className="card px-5 py-4">
          <p className="text-sm text-ink-500">
            Identity graph not initialized yet. Run a dedupe pipeline with{" "}
            <code className="font-mono text-gold-600">identity.enabled: true</code>{" "}
            in <code className="font-mono">goldenmatch.yml</code>, then come back.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="px-8 py-10 max-w-7xl mx-auto">
      <header className="mb-8">
        <p className="eyebrow mb-2">identities</p>
        <h1 className="display text-3xl text-ink-900">Identity graph</h1>
        <p className="mt-2 text-sm text-ink-500 max-w-2xl">
          Durable identities resolved across dedupe runs. Each row is an
          entity with member records, evidence edges, and a temporal event
          log.
        </p>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="identities" value={stats.data?.total.toString() ?? "—"} />
        <Stat label="shown" value={list.data?.items.length.toString() ?? "—"} />
        <Stat label="dataset filter" value={datasetFilter || "(all)"} />
        <Stat label="status filter" value={statusFilter || "(all)"} />
      </section>

      <section className="card px-5 py-4 mb-6 flex flex-wrap gap-3 items-end">
        <div>
          <label className="eyebrow block mb-1">dataset</label>
          <input
            className="input"
            value={datasetFilter}
            onChange={(e) => setDatasetFilter(e.target.value)}
            placeholder="(none)"
          />
        </div>
        <div>
          <label className="eyebrow block mb-1">status</label>
          <select
            className="input"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">all</option>
            <option value="active">active</option>
            <option value="merged_into">merged_into</option>
            <option value="retired">retired</option>
          </select>
        </div>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">identities</p>
          {list.data && list.data.items.length === 0 && (
            <p className="text-sm text-ink-500">No identities yet.</p>
          )}
          <ul className="divide-y divide-ink-100">
            {list.data?.items.map((row) => (
              <li
                key={row.entity_id}
                className={
                  "py-2 cursor-pointer hover:bg-ink-50 px-2 -mx-2 rounded transition-colors " +
                  (selected === row.entity_id ? "bg-ink-50" : "")
                }
                onClick={() => setSelected(row.entity_id)}
              >
                <IdentityRow row={row} />
              </li>
            ))}
          </ul>
        </div>

        <div className="card px-5 py-4">
          <p className="eyebrow mb-3">detail</p>
          {!selected && <p className="text-sm text-ink-500">Pick an identity to see members + events.</p>}
          {selected && detail.isPending && <p className="text-sm text-ink-500">Loading…</p>}
          {detail.data && (
            <IdentityDetail
              view={detail.data}
              onSplit={(record_ids) =>
                splitMut.mutate({ eid: detail.data!.entity_id, record_ids })
              }
            />
          )}
        </div>
      </section>
    </div>
  );
}

function IdentityRow({ row }: { row: IdentitySummary }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="font-mono text-[12px] text-ink-900 truncate">
          {row.entity_id}
        </div>
        <div className="text-[11px] text-ink-500">
          {row.status}
          {row.dataset && <span> · {row.dataset}</span>}
          {row.confidence != null && (
            <span> · conf {row.confidence.toFixed(3)}</span>
          )}
        </div>
      </div>
      <div className="text-[11px] text-ink-400 font-mono">
        {shortDate(row.updated_at)}
      </div>
    </div>
  );
}

function IdentityDetail({
  view,
  onSplit,
}: {
  view: IdentityView;
  onSplit: (record_ids: string[]) => void;
}) {
  const [selectedRecords, setSelectedRecords] = useState<Set<string>>(new Set());

  const toggle = (rid: string) => {
    const next = new Set(selectedRecords);
    if (next.has(rid)) next.delete(rid);
    else next.add(rid);
    setSelectedRecords(next);
  };

  return (
    <div>
      <div className="mb-4">
        <div className="font-mono text-[11px] text-ink-700 break-all">
          {view.entity_id}
        </div>
        <div className="text-[11px] text-ink-500 mt-1">
          {view.status} · {view.records.length} records · {view.edges.length} edges ·{" "}
          {view.events.length} events
        </div>
      </div>

      <div className="mb-5">
        <p className="eyebrow mb-2">members</p>
        <ul className="divide-y divide-ink-100">
          {view.records.map((r) => (
            <li key={r.record_id} className="py-1.5 flex items-center gap-2">
              <input
                type="checkbox"
                checked={selectedRecords.has(r.record_id)}
                onChange={() => toggle(r.record_id)}
              />
              <span className="font-mono text-[11px] truncate flex-1">
                {r.record_id}
              </span>
              <span className="text-[10px] text-ink-400">{r.source}</span>
            </li>
          ))}
        </ul>
        {selectedRecords.size > 0 && selectedRecords.size < view.records.length && (
          <button
            className="btn btn-secondary !text-xs mt-3"
            onClick={() => {
              onSplit(Array.from(selectedRecords));
              setSelectedRecords(new Set());
            }}
          >
            Split {selectedRecords.size} record(s) into new identity
          </button>
        )}
      </div>

      {view.events.length > 0 && (
        <div>
          <p className="eyebrow mb-2">history</p>
          <ul className="space-y-1.5 max-h-64 overflow-y-auto">
            {view.events
              .slice()
              .reverse()
              .map((ev) => (
                <li key={ev.event_id ?? ev.recorded_at} className="text-[11px]">
                  <span className="font-mono text-gold-600">{ev.kind}</span>
                  {ev.run_name && <span className="text-ink-400"> · {ev.run_name}</span>}
                  <span className="text-ink-400"> · {shortDate(ev.recorded_at)}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card px-4 py-3">
      <p className="eyebrow mb-1">{label}</p>
      <p className="text-xl font-mono text-ink-900">{value}</p>
    </div>
  );
}

function shortDate(iso: string): string {
  return iso.replace("T", " ").slice(0, 19);
}
