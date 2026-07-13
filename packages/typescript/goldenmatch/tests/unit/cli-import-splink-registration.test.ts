/**
 * cli-import-splink-registration.test.ts -- Task T4: the `import-splink`
 * command must be registered on the commander `program` -- the same
 * enumeration `scripts/emit_ts_surface.mjs` (the parity emitter) reads via
 * `program.commands.map((x) => x.name())`.
 *
 * Importing src/cli.ts is safe here: the `program.parseAsync` entry point is
 * guarded by an `import.meta.url === pathToFileURL(process.argv[1])` check
 * that never matches under vitest.
 */
import { describe, it, expect } from "vitest";
import { program } from "../../src/cli.js";

describe("cli.ts — program registration", () => {
  it("registers import-splink as a top-level command", () => {
    const names = program.commands.map((c) => c.name());
    expect(names).toContain("import-splink");
  });

  it("import-splink declares --output, --model-out, --strict", () => {
    const cmd = program.commands.find((c) => c.name() === "import-splink");
    expect(cmd).toBeDefined();
    const optionFlags = cmd!.options.map((o) => o.long);
    expect(optionFlags).toContain("--output");
    expect(optionFlags).toContain("--model-out");
    expect(optionFlags).toContain("--strict");
  });
});
