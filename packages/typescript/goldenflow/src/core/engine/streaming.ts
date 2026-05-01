/**
 * StreamProcessor — incremental transform processing.
 */

import type { GoldenFlowConfig, Row, TransformResult } from "../types.js";
import { TransformEngine } from "./transformer.js";

export class StreamProcessor {
  private readonly engine: TransformEngine;
  private _batchCount = 0;

  constructor(config?: Partial<GoldenFlowConfig>) {
    this.engine = new TransformEngine(config);
  }

  /** Transform a single record. */
  transformOne(record: Record<string, unknown>): TransformResult {
    return this.engine.transformDf([record]);
  }

  /** Transform a batch of rows. */
  transformBatch(rows: readonly Row[]): TransformResult {
    this._batchCount++;
    return this.engine.transformDf(rows);
  }

  /** Process rows in chunks, yielding TransformResult per chunk. */
  *streamRows(
    rows: readonly Row[],
    chunkSize = 10_000,
  ): Generator<TransformResult> {
    for (let start = 0; start < rows.length; start += chunkSize) {
      const batch = rows.slice(start, start + chunkSize);
      this._batchCount++;
      yield this.engine.transformDf(batch);
    }
  }

  get batchesProcessed(): number {
    return this._batchCount;
  }
}
