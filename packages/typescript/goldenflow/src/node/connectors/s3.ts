/**
 * AWS S3 connector for GoldenFlow (Node-only).
 *
 * Port of goldenflow/connectors/s3.py. Reads/writes CSV (or JSON) files on
 * S3 by round-tripping through a temp file and the local `file` connector.
 *
 * The AWS SDK is an OPTIONAL peer dependency. If `@aws-sdk/client-s3` is not
 * installed, every entry point throws a clear, actionable Error (fail-soft,
 * mirroring the Python `ImportError`).
 */

import { tmpdir } from "node:os";
import { join, extname } from "node:path";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import type { Row } from "../../core/types.js";
import { readFile, writeFile } from "./file.js";

const S3_INSTALL_HINT = "S3 support requires: npm install @aws-sdk/client-s3";

interface S3ClientLike {
  send(command: unknown): Promise<{ Body?: unknown }>;
}

interface S3Module {
  S3Client: new (config?: Record<string, unknown>) => S3ClientLike;
  GetObjectCommand: new (input: { Bucket: string; Key: string }) => unknown;
  PutObjectCommand: new (input: {
    Bucket: string;
    Key: string;
    Body: string | Buffer;
  }) => unknown;
}

async function loadS3(): Promise<S3Module> {
  try {
    // `as string` defeats tsup's static resolution so the optional peer dep
    // is only required at runtime.
    return (await import("@aws-sdk/client-s3" as string)) as S3Module;
  } catch {
    throw new Error(S3_INSTALL_HINT);
  }
}

/** Parse s3://bucket/key into [bucket, key]. */
export function parseS3Uri(uri: string): [string, string] {
  if (!uri.startsWith("s3://")) {
    throw new Error(`Invalid S3 URI: ${uri}. Must start with s3://`);
  }
  const path = uri.slice("s3://".length);
  const slash = path.indexOf("/");
  if (slash === -1) {
    throw new Error(`Invalid S3 URI: ${uri}. Must be s3://bucket/key`);
  }
  return [path.slice(0, slash), path.slice(slash + 1)];
}

async function streamToString(body: unknown): Promise<string> {
  if (body === null || body === undefined) return "";
  if (typeof body === "string") return body;
  // Node Buffer
  if (Buffer.isBuffer(body)) return body.toString("utf-8");
  // SDK v3 streaming body (has transformToString)
  const maybe = body as { transformToString?: () => Promise<string> };
  if (typeof maybe.transformToString === "function") {
    return maybe.transformToString();
  }
  // Async iterable of chunks (Node Readable)
  if (typeof (body as AsyncIterable<unknown>)[Symbol.asyncIterator] === "function") {
    const chunks: Buffer[] = [];
    for await (const chunk of body as AsyncIterable<Buffer | string>) {
      chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
    }
    return Buffer.concat(chunks).toString("utf-8");
  }
  return String(body);
}

/** Read a file from S3 into Row[]. */
export async function readS3(uri: string): Promise<Row[]> {
  const { S3Client, GetObjectCommand } = await loadS3();
  const [bucket, key] = parseS3Uri(uri);
  const ext = extname(key) || ".csv";

  const client = new S3Client();
  const response = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
  const content = await streamToString(response.Body);

  // mkdtempSync creates a uniquely-named, private (0700) directory — avoids
  // the insecure predictable-name-in-world-readable-tmpdir pattern.
  const tmpDir = mkdtempSync(join(tmpdir(), "goldenflow-"));
  const tmpPath = join(tmpDir, `data${ext}`);
  writeFileSync(tmpPath, content);
  try {
    return readFile(tmpPath);
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

/** Write Row[] to S3. */
export async function writeS3(rows: readonly Row[], uri: string): Promise<void> {
  const { S3Client, PutObjectCommand } = await loadS3();
  const [bucket, key] = parseS3Uri(uri);
  const ext = extname(key) || ".csv";

  const tmpDir = mkdtempSync(join(tmpdir(), "goldenflow-"));
  const tmpPath = join(tmpDir, `data${ext}`);
  writeFile(rows, tmpPath);
  let body: string;
  try {
    body = readFileSync(tmpPath, "utf-8");
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }

  const client = new S3Client();
  await client.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body }));
}
