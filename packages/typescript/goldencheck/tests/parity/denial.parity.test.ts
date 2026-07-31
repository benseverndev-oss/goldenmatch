/**
 * Cross-language parity for the denial-constraints engine.
 *
 * `tests/fixtures/denial_constraints.csv` (180 rows, string+int columns) exhibits
 * clear DCs; `denial_constraints.expected.json` is the rendered-DC list produced by
 * the Python engine:
 *   uv run python -c "from goldencheck.denial.mine import discover_denial_constraints; \
 *     from goldencheck.engine.reader import read_file; import json; \
 *     print(json.dumps([d.render() for d in \
 *       discover_denial_constraints(read_file('<csv>'))], ensure_ascii=False))"
 *
 * The fixture stays well under DEFAULT_SAMPLE (2000) / VALIDATION_SAMPLE (20000) so
 * neither engine draws its RNG — discovery is fully deterministic and the TS output
 * must equal the committed Python output exactly.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { readFile } from "../../src/node/reader.js";
import { discoverDenialConstraints } from "../../src/core/denial/mine.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(__dirname, "..", "fixtures");

describe("denial-constraints cross-language parity", () => {
  it("TS rendered DCs match the committed Python output byte-for-byte", () => {
    const csvPath = join(FIXTURES_DIR, "denial_constraints.csv");
    const expected = JSON.parse(
      readFileSync(join(FIXTURES_DIR, "denial_constraints.expected.json"), "utf-8"),
    ) as string[];

    const data = readFile(csvPath);
    const dcs = discoverDenialConstraints(data);
    const rendered = dcs.map((d) => d.render());

    expect(rendered).toEqual(expected);
  });
});
