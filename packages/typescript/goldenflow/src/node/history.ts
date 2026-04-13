/**
 * History — track transform runs.
 * Node-only module (uses node:fs, node:path, node:os).
 */

import { mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import type { RunRecord } from "../core/types.js";

function historyDir(): string {
  return join(homedir(), ".goldenflow", "history");
}

export function saveRun(record: RunRecord): string {
  const dir = historyDir();
  mkdirSync(dir, { recursive: true });
  const filePath = join(dir, `${record.runId}.json`);
  writeFileSync(filePath, JSON.stringify(record, null, 2));
  return filePath;
}

export function listRuns(limit = 20): RunRecord[] {
  const dir = historyDir();
  if (!existsSync(dir)) return [];

  const files = readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => ({ name, mtime: statSync(join(dir, name)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime)
    .slice(0, limit);

  const runs: RunRecord[] = [];
  for (const file of files) {
    try {
      const content = readFileSync(join(dir, file.name), "utf-8");
      runs.push(JSON.parse(content) as RunRecord);
    } catch (e) {
      console.warn(`[goldenflow:history] Skipping corrupt history file ${file.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return runs;
}

export function getRun(runId: string): RunRecord | null {
  const filePath = join(historyDir(), `${runId}.json`);
  if (!existsSync(filePath)) return null;
  return JSON.parse(readFileSync(filePath, "utf-8")) as RunRecord;
}

export function generateRunId(): string {
  const now = new Date();
  const ts = now.toISOString().replace(/[-:T]/g, "").slice(0, 15);
  const suffix = String(Date.now() % 10000).padStart(4, "0");
  return `${ts}_${suffix}`;
}
