/**
 * Google Cloud Storage connector for GoldenFlow (Node-only).
 *
 * Port of goldenflow/connectors/gcs.py. Reads/writes CSV (or JSON) files on
 * GCS by round-tripping through the local `file` connector.
 *
 * `@google-cloud/storage` is an OPTIONAL peer dependency. If it is not
 * installed, every entry point throws a clear, actionable Error (fail-soft,
 * mirroring the Python `ImportError`).
 */

import { tmpdir } from "node:os";
import { join, extname } from "node:path";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import type { Row } from "../../core/types.js";
import { readFile, writeFile } from "./file.js";

const GCS_INSTALL_HINT = "GCS support requires: npm install @google-cloud/storage";

interface BlobLike {
  download(): Promise<[Buffer]>;
  save(data: string | Buffer): Promise<void>;
}

interface BucketLike {
  file(name: string): BlobLike;
}

interface StorageLike {
  bucket(name: string): BucketLike;
}

interface GcsModule {
  Storage: new (config?: Record<string, unknown>) => StorageLike;
}

async function loadGcs(): Promise<GcsModule> {
  try {
    // `as string` defeats tsup's static resolution so the optional peer dep
    // is only required at runtime.
    return (await import("@google-cloud/storage" as string)) as GcsModule;
  } catch {
    throw new Error(GCS_INSTALL_HINT);
  }
}

/** Parse gs://bucket/path into [bucket, path]. */
export function parseGcsUri(uri: string): [string, string] {
  if (!uri.startsWith("gs://")) {
    throw new Error(`Invalid GCS URI: ${uri}. Must start with gs://`);
  }
  const path = uri.slice("gs://".length);
  const slash = path.indexOf("/");
  if (slash === -1) {
    throw new Error(`Invalid GCS URI: ${uri}. Must be gs://bucket/path`);
  }
  return [path.slice(0, slash), path.slice(slash + 1)];
}

/** Read a file from GCS into Row[]. */
export async function readGcs(uri: string): Promise<Row[]> {
  const { Storage } = await loadGcs();
  const [bucketName, blobName] = parseGcsUri(uri);
  const ext = extname(blobName) || ".csv";

  const storage = new Storage();
  const blob = storage.bucket(bucketName).file(blobName);
  const [buffer] = await blob.download();

  const tmpPath = join(tmpdir(), `goldenflow-${randomUUID()}${ext}`);
  writeFileSync(tmpPath, buffer);
  try {
    return readFile(tmpPath);
  } finally {
    try {
      unlinkSync(tmpPath);
    } catch {
      /* best-effort cleanup */
    }
  }
}

/** Write Row[] to GCS. */
export async function writeGcs(rows: readonly Row[], uri: string): Promise<void> {
  const { Storage } = await loadGcs();
  const [bucketName, blobName] = parseGcsUri(uri);
  const ext = extname(blobName) || ".csv";

  const tmpPath = join(tmpdir(), `goldenflow-${randomUUID()}${ext}`);
  writeFile(rows, tmpPath);
  let body: string;
  try {
    body = readFileSync(tmpPath, "utf-8");
  } finally {
    try {
      unlinkSync(tmpPath);
    } catch {
      /* best-effort cleanup */
    }
  }

  const storage = new Storage();
  const blob = storage.bucket(bucketName).file(blobName);
  await blob.save(body);
}
