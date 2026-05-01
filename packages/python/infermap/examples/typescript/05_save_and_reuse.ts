// 05 — Save a computed mapping to JSON and reuse it later.
//
// Run: npx tsx examples/typescript/05_save_and_reuse.ts
//
// Expensive to compute mappings on every request? Persist the result once,
// then re-hydrate it cheaply on subsequent runs. The JSON format is stable
// and compatible with the Python `from_config` loader.

import { map, mapResultToConfigJson, fromConfig } from "infermap";
import { writeFile, readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function main() {
  const tmp = await mkdtemp(join(tmpdir(), "infermap-ex-"));
  const configPath = join(tmp, "mapping.json");

  try {
    // 1. Compute once and save
    const result = map(
      {
        records: [
          { fname: "A", lname: "B", email_addr: "a@b.co" },
          { fname: "C", lname: "D", email_addr: "c@d.co" },
        ],
      },
      {
        records: [{ first_name: "", last_name: "", email: "" }],
      }
    );
    await writeFile(configPath, mapResultToConfigJson(result), "utf8");
    console.log(`Saved ${result.mappings.length} mappings to ${configPath}`);

    // 2. Later: reload without recomputing
    const savedText = await readFile(configPath, "utf8");
    const restored = fromConfig(savedText);

    console.log(`\nRestored ${restored.mappings.length} mappings:`);
    for (const m of restored.mappings) {
      console.log(`  ${m.source} → ${m.target} (${m.confidence})`);
    }
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
