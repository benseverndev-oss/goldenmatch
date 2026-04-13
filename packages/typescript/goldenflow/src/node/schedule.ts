import { readFile, writeFile } from "./connectors/file.js";
import { TransformEngine } from "../core/engine/transformer.js";
import { loadConfigFromString } from "../core/config/loader.js";
import { makeConfig } from "../core/types.js";
import { readFileSync } from "node:fs";
import { basename, extname, dirname, join } from "node:path";

function parseInterval(expr: string): number {
  const s = expr.trim().toLowerCase();
  const multipliers: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 };
  const unit = s.slice(-1);
  const mult = multipliers[unit];
  if (!mult) throw new Error(`Invalid interval: ${expr}. Use format like '5m', '1h', '30s'`);
  const num = parseFloat(s.slice(0, -1));
  if (isNaN(num)) throw new Error(`Invalid interval: ${expr}`);
  return num * mult;
}

export function runSchedule(
  filePath: string,
  options: { interval?: string; configPath?: string; outputDir?: string } = {},
): void {
  const intervalStr = options.interval ?? "1h";
  const seconds = parseInterval(intervalStr);
  const cfg = options.configPath
    ? loadConfigFromString(readFileSync(options.configPath, "utf-8"))
    : makeConfig();
  const engine = new TransformEngine(cfg);
  const outDir = options.outputDir ?? dirname(filePath);

  console.log(`Scheduled: transform ${filePath} every ${intervalStr}`);
  console.log("Press Ctrl+C to stop\n");

  let runCount = 0;
  const run = () => {
    runCount++;
    const ts = new Date().toTimeString().slice(0, 8);
    console.log(`Run #${runCount} at ${ts}`);
    try {
      const rows = readFile(filePath);
      const result = engine.transformDf(rows, filePath);
      const ext = extname(filePath);
      const stem = basename(filePath, ext);
      writeFile(result.rows as Record<string, unknown>[], join(outDir, `${stem}_transformed${ext}`));
      console.log(`  Done: ${result.manifest.records.length} transforms, ${result.manifest.errors.length} errors`);
    } catch (e) {
      console.error(`  Error: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  run();
  const timer = setInterval(run, seconds * 1000);
  process.on("SIGINT", () => { clearInterval(timer); console.log(`\nStopped after ${runCount} runs.`); process.exit(0); });
}
