import type {
  ClusterDetail,
  ClusterSummary,
  Project,
  RulesPayload,
} from "./types";

const json = async <T>(resp: Response): Promise<T> => {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json() as Promise<T>;
};

export const api = {
  project: (): Promise<Project> =>
    fetch("/api/v1/project").then((r) => json<Project>(r)),
  run: (name: string) =>
    fetch(`/api/v1/runs/${name}`).then((r) => json(r)),
  clusters: (
    name: string,
    cursor?: number,
  ): Promise<{ clusters: ClusterSummary[]; next_cursor: number | null }> =>
    fetch(`/api/v1/runs/${name}/clusters?cursor=${cursor ?? 0}`).then((r) =>
      json(r),
    ),
  cluster: (name: string, id: number): Promise<ClusterDetail> =>
    fetch(`/api/v1/runs/${name}/clusters/${id}`).then((r) =>
      json<ClusterDetail>(r),
    ),
  rules: (): Promise<RulesPayload> =>
    fetch("/api/v1/rules").then((r) => json<RulesPayload>(r)),
  putRules: (body: unknown) =>
    fetch("/api/v1/rules", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json(r)),
  saveRules: () =>
    fetch("/api/v1/rules/save", { method: "POST" }).then((r) => json(r)),
  preview: (body: unknown) =>
    fetch("/api/v1/preview", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json(r)),
  postLabel: (body: unknown) =>
    fetch("/api/v1/labels", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json(r)),
};
