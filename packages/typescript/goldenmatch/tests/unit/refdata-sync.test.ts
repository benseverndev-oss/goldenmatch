import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { GIVEN_NAME_ALIASES } from "../../src/core/refdata/givenNameAliases.js";

const here = dirname(fileURLToPath(import.meta.url));
const PY_JSON = join(
  here,
  "../../../../python/goldenmatch/goldenmatch/refdata/data/given_name_aliases.json",
);

describe("refdata sync: given_name_aliases", () => {
  it("TS const deep-equals the Python JSON payload (run scripts/sync_ts_refdata.mjs if this fails)", () => {
    const py = JSON.parse(readFileSync(PY_JSON, "utf-8"));
    expect(GIVEN_NAME_ALIASES).toEqual(py);
  });
});
