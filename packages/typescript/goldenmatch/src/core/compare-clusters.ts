/**
 * compare-clusters.ts — CCMS (Case Count Metric System) cluster comparison.
 * Edge-safe: no Node.js imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/compare_clusters.py.
 * Reference: Talburt et al., Case Count Metric System, arXiv:2601.02824v1.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ClusterCase = "unchanged" | "merged" | "partitioned" | "overlapping";

/**
 * The minimal shape compareClusters needs from a cluster: its member row ids.
 * `ClusterInfo` (the full dedupe cluster) is assignable to this, so existing
 * callers are unaffected; a bare `{ members }` (e.g. a parsed clusters-JSON
 * file) also satisfies it.
 */
export interface ClusterMembers {
  readonly members: readonly number[];
}

export interface CCMSResult {
  readonly unchanged: number;
  readonly merged: number;
  readonly partitioned: number;
  readonly overlapping: number;
  readonly twi: number;
  readonly clusterClassifications: Readonly<Record<number, ClusterCase>>;
  readonly cc1: number;
  readonly cc2: number;
  readonly rc: number;
  /** Singleton-cluster count in A / B (clusters of size 1). */
  readonly sc1: number;
  readonly sc2: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildMemberSets(
  clusters: ReadonlyMap<number, ClusterMembers>,
): { sets: Map<number, Set<number>>; ids: Set<number> } {
  const sets = new Map<number, Set<number>>();
  const ids = new Set<number>();
  for (const [cid, info] of clusters) {
    const memberSet = new Set<number>(info.members);
    sets.set(cid, memberSet);
    for (const m of memberSet) ids.add(m);
  }
  return { sets, ids };
}

function setsEqual(a: ReadonlySet<number>, b: ReadonlySet<number>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function isSubsetOf(
  sub: ReadonlySet<number>,
  sup: ReadonlySet<number>,
): boolean {
  if (sub.size > sup.size) return false;
  for (const v of sub) if (!sup.has(v)) return false;
  return true;
}

// ---------------------------------------------------------------------------
// compareClusters
// ---------------------------------------------------------------------------

/**
 * Compare two clustering outcomes via the CCMS framework.
 *
 * Classifies each cluster in A as unchanged, merged, partitioned, or
 * overlapping relative to B, and computes the Talburt-Wang Index:
 *   TWI = sqrt(CC1 * CC2) / V
 * where CC1/CC2 are cluster counts and V is the number of non-empty
 * A-to-B cluster intersections.
 *
 * Throws if the two cluster dicts do not cover the same row IDs.
 */
export function compareClusters(
  clustersA: ReadonlyMap<number, ClusterMembers>,
  clustersB: ReadonlyMap<number, ClusterMembers>,
): CCMSResult {
  const { sets: setsA, ids: idsA } = buildMemberSets(clustersA);
  const { sets: setsB, ids: idsB } = buildMemberSets(clustersB);

  if (idsA.size !== idsB.size) {
    throw new Error(
      `Cluster dicts cover different row IDs: ${idsA.size} vs ${idsB.size}`,
    );
  }
  for (const id of idsA) {
    if (!idsB.has(id)) {
      throw new Error(
        `Cluster dicts cover different row IDs (id ${id} only in A).`,
      );
    }
  }

  // Reverse lookup: row_id -> B cluster id
  const rowToB = new Map<number, number>();
  for (const [cid, members] of setsB) {
    for (const m of members) rowToB.set(m, cid);
  }

  const classifications: Record<number, ClusterCase> = {};
  let unchanged = 0;
  let merged = 0;
  let partitioned = 0;
  let overlapping = 0;
  let nonEmptyIntersections = 0;

  for (const [cidA, membersA] of setsA) {
    // Group A's members by which B-cluster they land in
    const bMapping = new Map<number, number[]>();
    for (const m of membersA) {
      const cidB = rowToB.get(m);
      if (cidB === undefined) continue;
      const list = bMapping.get(cidB);
      if (list !== undefined) list.push(m);
      else bMapping.set(cidB, [m]);
    }

    nonEmptyIntersections += bMapping.size;

    let caseKind: ClusterCase;
    if (bMapping.size === 1) {
      const cidB = bMapping.keys().next().value as number;
      const bMembers = setsB.get(cidB)!;
      if (setsEqual(bMembers, membersA)) {
        caseKind = "unchanged";
        unchanged++;
      } else {
        caseKind = "merged";
        merged++;
      }
    } else {
      // Multiple B clusters intersect this A cluster
      let allSubsets = true;
      for (const cidB of bMapping.keys()) {
        const bMembers = setsB.get(cidB)!;
        if (!isSubsetOf(bMembers, membersA)) {
          allSubsets = false;
          break;
        }
      }
      if (allSubsets) {
        caseKind = "partitioned";
        partitioned++;
      } else {
        caseKind = "overlapping";
        overlapping++;
      }
    }
    classifications[cidA] = caseKind;
  }

  const cc1 = setsA.size;
  const cc2 = setsB.size;
  const rc = idsA.size;

  let sc1 = 0;
  for (const s of setsA.values()) if (s.size === 1) sc1++;
  let sc2 = 0;
  for (const s of setsB.values()) if (s.size === 1) sc2++;

  let twi: number;
  if (nonEmptyIntersections > 0) {
    twi = Math.sqrt(cc1 * cc2) / nonEmptyIntersections;
  } else {
    twi = cc1 === 0 && cc2 === 0 ? 1.0 : 0.0;
  }

  return {
    unchanged,
    merged,
    partitioned,
    overlapping,
    twi,
    clusterClassifications: classifications,
    cc1,
    cc2,
    rc,
    sc1,
    sc2,
  };
}

// ---------------------------------------------------------------------------
// Wire helpers (parity with goldenmatch/mcp/server.py)
// ---------------------------------------------------------------------------

function round4(x: number): number {
  // Mirrors Python round(x, 4). Half-way ties at the 4th decimal are
  // effectively unreachable for these ratio / TWI values; any divergence
  // there sits within the suite's established 4-decimal parity tolerance.
  return Math.round(x * 1e4) / 1e4;
}

/**
 * Serialize a CCMSResult to the same JSON summary dict Python's
 * `CompareResult.summary()` returns (snake_case keys, TWI + percentages
 * rounded to 4dp). This is the MCP `compare_clusters` tool's wire output.
 */
export function ccmsSummary(r: CCMSResult): Record<string, number> {
  const total = r.cc1 || 1;
  return {
    unchanged: r.unchanged,
    merged: r.merged,
    partitioned: r.partitioned,
    overlapping: r.overlapping,
    rc: r.rc,
    cc1: r.cc1,
    cc2: r.cc2,
    sc1: r.sc1,
    sc2: r.sc2,
    twi: round4(r.twi),
    unchanged_pct: round4(r.unchanged / total),
    merged_pct: round4(r.merged / total),
    partitioned_pct: round4(r.partitioned / total),
    overlapping_pct: round4(r.overlapping / total),
  };
}

/**
 * Parse a clusters-JSON payload into the member-map compareClusters takes.
 *
 * Mirrors goldenmatch/mcp/server.py::_load_clusters_json: accepts either a
 * bare `{clusterId: ...}` mapping or a `{"clusters": {...}}` wrapper, and
 * cluster values that are either `{"members": [...]}` or a bare `[...]` list.
 * Cluster ids and member ids are coerced to numbers.
 */
export function parseClustersJson(
  data: unknown,
): Map<number, ClusterMembers> {
  const isObj = (v: unknown): v is Record<string, unknown> =>
    v !== null && typeof v === "object" && !Array.isArray(v);

  const raw: unknown =
    isObj(data) && "clusters" in data ? data.clusters : data;
  if (!isObj(raw)) {
    throw new Error(
      "clusters JSON must be an object mapping cluster id -> members",
    );
  }

  const out = new Map<number, ClusterMembers>();
  for (const [k, v] of Object.entries(raw)) {
    const members: unknown = Array.isArray(v)
      ? v
      : isObj(v)
        ? v.members
        : undefined;
    if (!Array.isArray(members)) {
      throw new Error(`cluster ${k} has no members list`);
    }
    out.set(Number(k), { members: members.map((m) => Number(m)) });
  }
  return out;
}
