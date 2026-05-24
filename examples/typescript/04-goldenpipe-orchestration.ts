/**
 * 04 — GoldenPipe orchestration, TypeScript edition.
 *
 * Chains GoldenCheck (scan) -> GoldenFlow (transform) -> GoldenMatch (dedupe)
 * through one adaptive pipeline. The whole runner is async because
 * GoldenMatch's `dedupe` is async, so `runDf` returns a promise.
 *
 * Run:
 *     npm install goldenpipe   # pulls goldencheck/goldenflow/goldenmatch
 *     npx tsx 04-goldenpipe-orchestration.ts
 */
import {
  runDf,
  makePipelineConfig,
  makeStageSpec,
  PipeStatus,
} from "goldenpipe";

const rows = [
  { first_name: "John", last_name: "Smith", email: "JOHN@example.com  " },
  { first_name: "Jon",  last_name: "Smith", email: "john@example.com" },
  { first_name: "Jane", last_name: "Doe",   email: "jane@example.com" },
];

// ── Zero-config chain: goldencheck.scan -> goldenflow.transform -> goldenmatch.dedupe
const result = await runDf(rows);

console.log("status:", result.status);          // "success"
console.log("input rows:", result.inputRows);    // 3
console.log("golden:", result.artifacts.golden); // canonical records
console.log("unique:", result.artifacts.unique); // distinct records

// Per-stage status sequence + any decisions the pipeline made.
for (const [name, stage] of Object.entries(result.stages)) {
  console.log(`  stage ${name}: ${stage.status}`);
}
if (result.status !== PipeStatus.SUCCESS) {
  console.error("errors:", result.errors);
}

// ── Custom config: tighten the dedupe threshold, skip the transform stage.
const config = makePipelineConfig({
  pipeline: "check-and-dedupe",
  stages: [
    "goldencheck.scan",
    makeStageSpec({ use: "goldenmatch.dedupe", config: { threshold: 0.9 } }),
  ],
});

const tuned = await runDf(rows, config);
console.log("\ntuned status:", tuned.status);
console.log("tuned clusters:", tuned.artifacts.clusters);
