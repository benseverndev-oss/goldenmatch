/**
 * Directory watcher — polls for file changes and runs scans.
 * Port of goldencheck/engine/watcher.py. Node-only (uses node:fs).
 */

import { statSync, readdirSync } from "node:fs";
import { join, extname } from "node:path";

export interface WatchOptions {
  /** Poll interval in milliseconds. Default: 30000 (30s). */
  interval?: number | undefined;
  /** File glob pattern. Default: "*.csv". */
  pattern?: string | undefined;
  /** Exit on this severity or higher. */
  exitOn?: "error" | "warning" | undefined;
  /** Callback when a file changes. */
  onFileChanged: (filePath: string) => void;
  /** Signal to stop watching. */
  signal?: AbortSignal | undefined;
}

/**
 * Watch a directory for file changes by polling mtimes.
 * Returns a promise that resolves when watching stops.
 */
export async function watchDirectory(
  dirPath: string,
  options: WatchOptions,
): Promise<void> {
  const interval = options.interval ?? 30_000;
  const ext = options.pattern?.replace("*", "") ?? ".csv";
  const mtimes = new Map<string, number>();

  // Initial scan — record mtimes
  for (const file of listFiles(dirPath, ext)) {
    mtimes.set(file, getModifiedTime(file));
  }

  return new Promise<void>((resolve) => {
    const check = () => {
      if (options.signal?.aborted) {
        resolve();
        return;
      }

      const files = listFiles(dirPath, ext);
      for (const file of files) {
        const mtime = getModifiedTime(file);
        const prevMtime = mtimes.get(file);
        if (prevMtime === undefined || mtime > prevMtime) {
          mtimes.set(file, mtime);
          if (prevMtime !== undefined) {
            // File changed (not first scan)
            options.onFileChanged(file);
          }
        }
      }

      setTimeout(check, interval);
    };

    setTimeout(check, interval);
  });
}

function listFiles(dirPath: string, ext: string): string[] {
  try {
    return readdirSync(dirPath)
      .filter((f) => extname(f).toLowerCase() === ext)
      .map((f) => join(dirPath, f));
  } catch {
    return [];
  }
}

function getModifiedTime(filePath: string): number {
  try {
    return statSync(filePath).mtimeMs;
  } catch {
    return 0;
  }
}
