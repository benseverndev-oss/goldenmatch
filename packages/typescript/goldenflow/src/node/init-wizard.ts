import { createInterface } from "node:readline";
import { readFile } from "./connectors/file.js";
import { profileDataframe } from "../core/engine/profiler-bridge.js";
import { selectTransforms } from "../core/engine/selector.js";
import { saveConfigToString } from "../core/config/loader.js";
import { makeConfig } from "../core/types.js";
import type { TransformSpec } from "../core/types.js";
import { writeFileSync } from "node:fs";

function ask(rl: ReturnType<typeof createInterface>, question: string): Promise<string> {
  return new Promise((resolve) => rl.question(question, resolve));
}

export async function runWizard(dataPath?: string, outputPath = "goldenflow.yaml"): Promise<void> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });

  try {
    console.log("GoldenFlow Setup Wizard\n");

    const filePath = dataPath ?? await ask(rl, "Path to your data file: ");
    console.log(`\nProfiling ${filePath}...`);

    const rows = readFile(filePath);
    const profile = profileDataframe(rows, filePath);

    console.log(`\n${profile.rowCount} rows, ${profile.columnCount} columns\n`);

    const columnTransforms: Record<string, string[]> = {};
    for (const col of profile.columns) {
      const selected = selectTransforms(col);
      const names = selected.map(t => t.name);
      columnTransforms[col.name] = names;
      console.log(`  ${col.name}: ${col.inferredType} | suggested: ${names.slice(0, 3).join(", ") || "none"}`);
    }

    console.log("\nConfigure transforms per column:\n");
    const transforms: TransformSpec[] = [];

    for (const [colName, suggested] of Object.entries(columnTransforms)) {
      if (suggested.length === 0) continue;
      const answer = await ask(rl, `  Apply [${suggested.join(", ")}] to ${colName}? (Y/n) `);
      if (answer.toLowerCase() !== "n") {
        transforms.push({ column: colName, ops: suggested });
      }
    }

    const config = makeConfig({ source: filePath, transforms });
    const yaml = saveConfigToString(config);
    writeFileSync(outputPath, yaml);
    console.log(`\nConfig saved to ${outputPath}`);
    console.log(`Run: goldenflow-js transform ${filePath} -c ${outputPath}`);
  } finally {
    rl.close();
  }
}
