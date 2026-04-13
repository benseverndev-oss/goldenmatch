import { readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";
import { readFile, writeFile } from "./connectors/file.js";
import { TransformEngine } from "../core/engine/transformer.js";
import { loadConfigFromString } from "../core/config/loader.js";
import { makeConfig } from "../core/types.js";
import { readFileSync } from "node:fs";

export function watchDirectory(
  dirPath: string,
  options: { configPath?: string; outputDir?: string; interval?: number } = {},
): void {
  const interval = options.interval ?? 2.0;
  const cfg = options.configPath
    ? loadConfigFromString(readFileSync(options.configPath, "utf-8"))
    : makeConfig();
  const engine = new TransformEngine(cfg);
  const outDir = options.outputDir ?? dirPath;
  const seen = new Map<string, number>(); // path -> mtime

  console.log(`Watching ${dirPath} (interval: ${interval}s)`);
  console.log("Press Ctrl+C to stop\n");

  const poll = () => {
    try {
      const files = readdirSync(dirPath).filter(f => extname(f).toLowerCase() === ".csv");
      for (const file of files) {
        if (file.includes("_transformed")) continue;
        const fullPath = join(dirPath, file);
        const mtime = statSync(fullPath).mtimeMs;
        if (!seen.has(fullPath) || seen.get(fullPath)! < mtime) {
          console.log(`Detected: ${file}`);
          try {
            const rows = readFile(fullPath);
            const result = engine.transformDf(rows, fullPath);
            const stem = file.replace(extname(file), "");
            writeFile(result.rows as Record<string, unknown>[], join(outDir, `${stem}_transformed.csv`));
            console.log(`  Transformed: ${result.manifest.records.length} transforms applied`);
          } catch (e) {
            console.error(`  Error: ${e instanceof Error ? e.message : String(e)}`);
          }
          seen.set(fullPath, mtime);
        }
      }
    } catch (e) {
      console.error(`Watch error: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  poll();
  const timer = setInterval(poll, interval * 1000);
  process.on("SIGINT", () => { clearInterval(timer); console.log("\nWatch stopped."); process.exit(0); });
}
