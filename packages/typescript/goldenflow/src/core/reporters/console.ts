import type { Manifest, DatasetProfile, DiffResult } from "../types.js";

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const CYAN = "\x1b[36m";
const MAGENTA = "\x1b[35m";
const RESET = "\x1b[0m";

export function printProfile(profile: DatasetProfile): void {
  console.log(`\n${BOLD}Profile: ${profile.filePath || "<dataframe>"}${RESET}\n`);
  console.log(`  ${"Column".padEnd(20)} ${"Type".padEnd(12)} ${"Nulls".padEnd(15)} ${"Unique".padEnd(10)} Sample`);
  console.log(`  ${"─".repeat(20)} ${"─".repeat(12)} ${"─".repeat(15)} ${"─".repeat(10)} ${"─".repeat(20)}`);

  for (const col of profile.columns) {
    const pct = (col.nullPct * 100).toFixed(0);
    console.log(
      `  ${CYAN}${col.name.padEnd(20)}${RESET} ${GREEN}${col.inferredType.padEnd(12)}${RESET} ${YELLOW}${`${col.nullCount} (${pct}%)`.padEnd(15)}${RESET} ${MAGENTA}${String(col.uniqueCount).padEnd(10)}${RESET} ${DIM}${col.sampleValues.slice(0, 3).join(", ")}${RESET}`
    );
  }

  console.log(`\n${BOLD}${profile.rowCount}${RESET} rows, ${BOLD}${profile.columnCount}${RESET} columns`);
}

export function printManifest(manifest: Manifest): void {
  if (manifest.records.length === 0 && manifest.errors.length === 0) {
    console.log(`${DIM}No transforms applied.${RESET}`);
    return;
  }

  console.log(`\n${BOLD}Transforms Applied${RESET}\n`);
  console.log(`  ${"Column".padEnd(20)} ${"Transform".padEnd(22)} ${"Affected".padEnd(12)} ${"Before".padEnd(20)} After`);
  console.log(`  ${"─".repeat(20)} ${"─".repeat(22)} ${"─".repeat(12)} ${"─".repeat(20)} ${"─".repeat(20)}`);

  for (const r of manifest.records) {
    const before = r.sampleBefore.slice(0, 2).join(", ");
    const after = r.sampleAfter.slice(0, 2).join(", ");
    console.log(
      `  ${CYAN}${r.column.padEnd(20)}${RESET} ${GREEN}${r.transform.padEnd(22)}${RESET} ${YELLOW}${`${r.affectedRows}/${r.totalRows}`.padEnd(12)}${RESET} ${DIM}${before.padEnd(20)}${RESET} ${BOLD}${after}${RESET}`
    );
  }

  if (manifest.errors.length > 0) {
    console.log(`\n${RED}${BOLD}${manifest.errors.length} errors:${RESET}`);
    for (const e of manifest.errors) {
      console.log(`  ${RED}${e.column}${RESET} / ${e.transform}: ${e.error}`);
    }
  }
}

export function printDiff(diff: DiffResult): void {
  console.log(`Rows: ${diff.rowCountBefore} → ${diff.rowCountAfter}`);
  console.log(`Total changes: ${BOLD}${diff.totalChanges}${RESET}`);
  if (diff.addedColumns.length) console.log(`Added columns: ${GREEN}${diff.addedColumns.join(", ")}${RESET}`);
  if (diff.removedColumns.length) console.log(`Removed columns: ${RED}${diff.removedColumns.join(", ")}${RESET}`);
  if (diff.changedColumns.length) console.log(`Changed columns: ${YELLOW}${diff.changedColumns.join(", ")}${RESET}`);
}
