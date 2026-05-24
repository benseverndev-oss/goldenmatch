/**
 * CLI smoke tests — exercise the built dist/cli.cjs via a subprocess.
 * Verifies `stages`, `validate`, `init`, and `run` produce expected output.
 */

import { describe, it, expect, afterAll } from "vitest";
import { execFileSync } from "node:child_process";
import { writeFileSync, rmSync, mkdtempSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { csv as sample } from "./_sample.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI = join(__dirname, "..", "..", "dist", "cli.cjs");
const dir = mkdtempSync(join(tmpdir(), "gp-cli-"));

function cli(args: string[]): string {
  return execFileSync("node", [CLI, ...args], { cwd: dir, encoding: "utf8" });
}

afterAll(() => rmSync(dir, { recursive: true, force: true }));

// The CLI suite shells out to the built dist/cli.cjs. Skip (don't fail) when
// it isn't built yet — e.g. the publish workflow runs vitest before
// `pnpm run build`. It runs in local dev / CI where the build precedes tests.
describe.skipIf(!existsSync(CLI))("goldenpipe CLI", () => {
  it("stages lists the built-in suite stages", () => {
    const out = cli(["stages"]);
    expect(out).toContain("goldencheck.scan");
    expect(out).toContain("goldenflow.transform");
    expect(out).toContain("goldenmatch.dedupe");
  });

  it("init writes a goldenpipe.yml without the load stage", () => {
    cli(["init"]);
    const yml = readFileSync(join(dir, "goldenpipe.yml"), "utf8");
    expect(yml).toContain("pipeline: my-pipeline");
    expect(yml).toContain("- goldenmatch.dedupe");
    expect(yml).not.toContain("- load");
  });

  it("validate resolves a valid config", () => {
    const p = join(dir, "valid.yml");
    writeFileSync(p, ["pipeline: t", "stages:", "  - goldencheck.scan", "  - goldenmatch.dedupe"].join("\n"));
    const out = cli(["validate", "-c", p]);
    expect(out).toContain("Valid");
    expect(out).toContain("goldenmatch.dedupe");
  });

  it("run executes the chain on a CSV file", () => {
    const p = join(dir, "people.csv");
    writeFileSync(p, sample);
    const out = cli(["run", p]);
    expect(out).toContain("SUCCESS");
    expect(out).toContain("goldenmatch.dedupe");
  });
});
