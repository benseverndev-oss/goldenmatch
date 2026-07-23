/**
 * retrieve-similar.ts — Semantic retrieval (#1089).
 *
 * Edge-safe: no `node:` imports. Ports `goldenmatch/core/retrieval.py`
 * (`retrieve_similar_records`) — the read side of the RAG
 * entity-canonicalization epic. Embed a free-text query + a column of a corpus,
 * run ANN cosine search, and return the top-K most similar records with scores.
 *
 * Built on the existing edge-safe primitives — the `Embedder` (`embedder.ts`)
 * and `ANNBlocker` (`ann-blocker.ts`, brute-force cosine with an optional
 * hnsw fast-path). It adds NO new embedding or ANN implementation.
 *
 * EDGE-MODEL CAVEAT (critical): unlike Python — whose default `"inhouse"`
 * embedder is a bundled, zero-config, no-cloud model — the TS surface carries
 * only the embedding KERNEL (`goldenembed-wasm`), NOT a bundled model, and the
 * HTTP `Embedder` needs a provider + credentials. So the model is
 * CALLER-SUPPLIED: `retrieveSimilar` REQUIRES an explicit `embedder` and throws
 * a clear error when none is provided. There is no silent default model.
 */

import { ANNBlocker } from "./ann-blocker.js";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * The minimal embedder contract `retrieveSimilar` needs. Both the HTTP
 * `Embedder` (`getEmbedder(...)`) and a `goldenembed-wasm` embedder can be
 * adapted to this shape. `embedColumn` returns one vector per input value.
 */
export interface EmbedderLike {
  embedColumn(
    values: readonly (string | null | undefined)[],
    cacheKey?: string,
  ): Promise<readonly Float32Array[]>;
}

/** One record returned by `retrieveSimilar` (mirrors Python `RetrievedRecord`). */
export interface RetrievedRecord {
  readonly rowId: number;
  readonly score: number;
  readonly record: Record<string, unknown>;
}

export interface RetrieveSimilarOptions {
  /** Maximum records to return, ranked by similarity desc (default 20). */
  readonly k?: number;
  /** Minimum cosine similarity in [-1, 1] a record must reach (default 0.0). */
  readonly threshold?: number;
  /** Optional `{column: value}` equality pre-filter applied BEFORE embedding. */
  readonly filters?: Readonly<Record<string, unknown>> | null;
  /**
   * The embedder to use — REQUIRED. The TS surface has no bundled model
   * (see the edge-model caveat above), so the caller must supply one.
   */
  readonly embedder?: EmbedderLike;
}

/** Raised when the caller-supplied-embedder contract is violated. */
export class RetrieveSimilarError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RetrieveSimilarError";
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Serialize a record for the wire (Python `RetrievedRecord.as_dict`). */
export function retrievedRecordToDict(
  r: RetrievedRecord,
): Record<string, unknown> {
  return {
    row_id: r.rowId,
    score: Math.round(r.score * 1e4) / 1e4,
    record: r.record,
  };
}

// ---------------------------------------------------------------------------
// Retrieval
// ---------------------------------------------------------------------------

/**
 * Retrieve the top-`k` rows in `rows` most similar to `query`.
 *
 * Faithful to Python `retrieve_similar_records`:
 *  - empty when the query is blank, the (filtered) corpus is empty, or nothing
 *    clears `threshold`;
 *  - a filter on a column not present yields no results;
 *  - `__row_id__` is used for the returned id when present, else row position
 *    (within the filtered corpus);
 *  - `__`-prefixed keys are stripped from the returned record;
 *  - results are ranked highest cosine similarity first.
 *
 * @throws RetrieveSimilarError if `column` is not in the corpus, or if no
 *   `embedder` is supplied (the caller-supplied-embedder contract).
 */
export async function retrieveSimilar(
  rows: readonly Readonly<Record<string, unknown>>[],
  query: string,
  column: string,
  options: RetrieveSimilarOptions = {},
): Promise<RetrievedRecord[]> {
  const k = options.k ?? 20;
  const threshold = options.threshold ?? 0.0;
  const { filters, embedder } = options;

  if (embedder === undefined || embedder === null) {
    throw new RetrieveSimilarError(
      "retrieveSimilar requires an explicit embedder: the TS surface carries " +
        "only the embedding kernel (goldenembed-wasm), not a bundled model. " +
        "Pass options.embedder — e.g. getEmbedder({ provider, apiKey }) (HTTP) " +
        "or a goldenembed-wasm embedder built from caller-supplied weights.",
    );
  }

  // Column presence is checked against the first row's keys (the corpus schema).
  if (rows.length > 0 && !Object.prototype.hasOwnProperty.call(rows[0], column)) {
    throw new RetrieveSimilarError(
      `retrieveSimilar: column '${column}' not in dataframe ` +
        `(have ${Object.keys(rows[0] ?? {}).join(", ")})`,
    );
  }

  if (!query || rows.length === 0) return [];

  // Metadata pre-filter (equality on each supplied column) BEFORE embedding.
  let work: readonly Readonly<Record<string, unknown>>[] = rows;
  if (filters && Object.keys(filters).length > 0) {
    const cols = Object.keys(filters);
    // A filter on a column absent from the corpus yields no results.
    if (rows.length > 0) {
      for (const col of cols) {
        if (!Object.prototype.hasOwnProperty.call(rows[0], col)) return [];
      }
    }
    work = rows.filter((row) => cols.every((col) => row[col] === filters[col]));
  }
  if (work.length === 0) return [];

  const values: (string | null)[] = work.map((row) => {
    const v = row[column];
    return v === null || v === undefined ? "" : String(v);
  });

  let corpus: readonly Float32Array[];
  let qVecs: readonly Float32Array[];
  try {
    corpus = await embedder.embedColumn(values, `retrieve:${column}`);
    qVecs = await embedder.embedColumn([String(query)], "retrieve_q");
  } catch {
    // Embedding failed (network / credentials / model) — Python returns [].
    return [];
  }
  const qVec = qVecs[0];
  if (!qVec) return [];

  const blocker = new ANNBlocker({ topK: k });
  blocker.buildIndex(corpus);
  const neighbors = blocker.queryOne(qVec); // [[position, cosine], ...] desc

  const hasRowId =
    work.length > 0 &&
    Object.prototype.hasOwnProperty.call(work[0], "__row_id__");

  const out: RetrievedRecord[] = [];
  for (const [pos, score] of neighbors) {
    if (score < threshold) continue;
    if (pos < 0 || pos >= work.length) continue;
    const srcRow = work[pos]!;
    const record: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(srcRow)) {
      if (!key.startsWith("__")) record[key] = val;
    }
    let rowId: number;
    if (hasRowId) {
      const raw = srcRow["__row_id__"];
      rowId = typeof raw === "number" ? raw : Number(raw);
    } else {
      rowId = pos;
    }
    out.push({ rowId, score, record });
  }
  return out;
}
