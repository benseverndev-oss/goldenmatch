/**
 * cli-interactive.test.ts -- `init` / `label` / `review` (parity batch 7).
 *
 * These commands are interactive, which is exactly why the loops take an
 * injectable `Ask`: a scripted session tests the real decision logic with no TTY.
 * The Python originals call Rich prompts inline and have no loop tests -- that is
 * the wart this port deliberately does not reproduce.
 */
import { describe, it, expect } from "vitest";
import { scriptedAsk, askChoice, askYesNo, askWithDefault, renderPair } from "../../src/node/interactive.js";
import {
  selectPairs,
  runLabelSession,
  runReviewSession,
  BORDERLINE_ANCHOR,
} from "../../src/node/label-session.js";
import { runWizard, suggestTransforms, suggestScorer, toYaml } from "../../src/node/config-wizard.js";
import type { Row, ScoredPair } from "../../src/core/types.js";

const ROWS = new Map<number, Row>([
  [0, { name: "Alice Nguyen", email: "a@x.com" }],
  [1, { name: "Alice Nguyen", email: "a@x.com" }],
  [2, { name: "Bob Okafor", email: "b@y.com" }],
  [3, { name: "Carol Petrov", email: "c@z.com" }],
]);
const COLS = ["name", "email"];

describe("prompt helpers", () => {
  it("askChoice re-asks until the answer is allowed", async () => {
    const invalid: string[] = [];
    const got = await askChoice(scriptedAsk(["maybe", "wat", "y"]), "> ", ["y", "n"], "n", (r) =>
      invalid.push(r),
    );
    expect(got).toBe("y");
    expect(invalid).toEqual(["maybe", "wat"]);
  });

  it("askChoice takes the fallback on empty input (EOF / piped stdin)", async () => {
    // Without this, a closed stdin would spin forever re-prompting.
    expect(await askChoice(scriptedAsk([""]), "> ", ["y", "n"], "q")).toBe("q");
  });

  it("askYesNo and askWithDefault honor their defaults on empty input", async () => {
    expect(await askYesNo(scriptedAsk([""]), "ok?", true)).toBe(true);
    expect(await askYesNo(scriptedAsk([""]), "ok?", false)).toBe(false);
    expect(await askYesNo(scriptedAsk(["yes"]), "ok?", false)).toBe(true);
    expect(await askWithDefault(scriptedAsk([""]), "name", "mk_1")).toBe("mk_1");
    expect(await askWithDefault(scriptedAsk(["custom"]), "name", "mk_1")).toBe("custom");
  });

  it("renderPair marks agreeing non-empty fields", () => {
    const out = renderPair(ROWS.get(0)!, ROWS.get(1)!, COLS, "t");
    expect(out).toContain("=");
    const differing = renderPair(ROWS.get(0)!, ROWS.get(2)!, COLS, "t");
    expect(differing.split("\n").filter((l) => l.endsWith("="))).toHaveLength(0);
  });
});

describe("selectPairs (Python strategy parity)", () => {
  const pairs: ScoredPair[] = [
    { idA: 0, idB: 1, score: 0.99 },
    { idA: 0, idB: 2, score: 0.86 },
    { idA: 1, idB: 3, score: 0.4 },
  ];

  it("borderline orders by distance from 0.85", () => {
    expect(BORDERLINE_ANCHOR).toBe(0.85);
    expect(selectPairs(pairs, "borderline").map((p) => p.score)).toEqual([0.86, 0.99, 0.4]);
  });

  it("hardest orders by ascending score", () => {
    expect(selectPairs(pairs, "hardest").map((p) => p.score)).toEqual([0.4, 0.86, 0.99]);
  });

  it("random permutes without losing or duplicating pairs", () => {
    const out = selectPairs(pairs, "random", () => 0.5);
    expect(out).toHaveLength(3);
    expect(new Set(out.map((p) => `${p.idA}:${p.idB}`)).size).toBe(3);
  });

  it("does not mutate the caller's array", () => {
    const original = [...pairs];
    selectPairs(pairs, "hardest");
    expect(pairs).toEqual(original);
  });
});

describe("label session", () => {
  const pairs: ScoredPair[] = [
    { idA: 0, idB: 1, score: 0.95 },
    { idA: 0, idB: 2, score: 0.7 },
    { idA: 1, idB: 3, score: 0.6 },
  ];
  const base = { pairs, rowsById: ROWS, displayColumns: COLS };

  it("records y/n and counts skips", async () => {
    const r = await runLabelSession({ ...base, target: 3, ask: scriptedAsk(["y", "s", "n"]) });
    expect(r.labels).toEqual([
      { id_a: 0, id_b: 1, label: 1, score: 0.95 },
      { id_a: 1, id_b: 3, label: 0, score: 0.6 },
    ]);
    expect(r.skipped).toBe(1);
    expect(r.quit).toBe(false);
  });

  it("q stops immediately but KEEPS what was already labeled", async () => {
    const r = await runLabelSession({ ...base, target: 3, ask: scriptedAsk(["y", "q"]) });
    expect(r.quit).toBe(true);
    expect(r.labels).toHaveLength(1);
  });

  it("stops at the target count without consuming more pairs", async () => {
    const r = await runLabelSession({ ...base, target: 1, ask: scriptedAsk(["y", "y", "y"]) });
    expect(r.labels).toHaveLength(1);
  });

  it("skips already-labeled pairs in EITHER orientation (--append)", async () => {
    // Python checks (a,b) and (b,a); a one-way check would re-serve the pair.
    const r = await runLabelSession({
      ...base,
      target: 3,
      ask: scriptedAsk(["y", "y"]),
      existing: new Set(["1:0"]), // reversed form of the first pair
    });
    expect(r.labels.map((l) => [l.id_a, l.id_b])).toEqual([
      [0, 2],
      [1, 3],
    ]);
  });

  it("rounds the recorded score to 4dp like Python", async () => {
    const r = await runLabelSession({
      pairs: [{ idA: 0, idB: 1, score: 0.123456789 }],
      rowsById: ROWS,
      displayColumns: COLS,
      target: 1,
      ask: scriptedAsk(["y"]),
    });
    expect(r.labels[0]!.score).toBe(0.1235);
  });
});

describe("review session", () => {
  const items = [
    { idA: 0, idB: 1, score: 0.9 },
    { idA: 0, idB: 2, score: 0.8 },
  ];
  const base = { items, rowsById: ROWS, displayColumns: COLS };

  it("maps y/n to approve/reject", async () => {
    const r = await runReviewSession({ ...base, ask: scriptedAsk(["y", "n"]) });
    expect(r.decisions).toEqual([
      { idA: 0, idB: 1, decision: "approve" },
      { idA: 0, idB: 2, decision: "reject" },
    ]);
  });

  it("a mid-session quit still returns the decisions already made", async () => {
    // The CLI persists AFTER the loop, so losing these would discard real work.
    const r = await runReviewSession({ ...base, ask: scriptedAsk(["y", "q"]) });
    expect(r.quit).toBe(true);
    expect(r.decisions).toHaveLength(1);
  });

  it("honors --limit", async () => {
    const r = await runReviewSession({ ...base, ask: scriptedAsk(["y", "y"]), limit: 1 });
    expect(r.decisions).toHaveLength(1);
  });
});

describe("config wizard: heuristic parity with Python", () => {
  it.each([
    ["first_name", ["lowercase", "strip", "normalize_whitespace"], "jaro_winkler"],
    ["email", ["lowercase", "strip"], "levenshtein"],
    ["phone", ["digits_only"], "exact"],
    ["zip_code", ["strip", "substring:0:5"], "exact"],
    ["street_address", ["lowercase", "strip", "normalize_whitespace"], "token_sort"],
    ["widget_id", ["strip"], "jaro_winkler"],
  ])("%s -> transforms + scorer", (col, transforms, scorer) => {
    expect(suggestTransforms(col)).toEqual(transforms);
    expect(suggestScorer(col)).toBe(scorer);
  });

  it("keyword matching is substring-based and case-insensitive, as in Python", () => {
    expect(suggestScorer("CustomerEMAIL")).toBe("levenshtein");
    expect(suggestTransforms("Mobile_Number")).toEqual(["digits_only"]);
  });
});

describe("config wizard: scripted end-to-end", () => {
  it("builds an exact-matchkey config", async () => {
    const cfg = await runWizard(
      scriptedAsk([
        "dedupe",        // mode
        "people.csv",    // input path
        "",              // source label -> default
        "",              // add another file? -> no
        "",              // matchkey name -> mk_1
        "exact",         // type
        "email",         // field
        "",              // use suggested transforms -> yes
        "",              // add another field -> no
        "",              // add another matchkey -> no
        "",              // configure blocking? -> default (no weighted -> false)
        "",              // output format -> csv
        "",              // output dir -> ./output
        "",              // run name -> empty
      ]),
    );
    expect(cfg.matchkeys).toEqual([
      { name: "mk_1", type: "exact", fields: [{ field: "email", transforms: ["lowercase", "strip"] }] },
    ]);
    expect(cfg.blocking).toBeUndefined();
    expect(cfg.output).toEqual({ format: "csv", directory: "./output" });
  });

  it("a weighted matchkey forces blocking configuration without asking", async () => {
    const cfg = await runWizard(
      scriptedAsk([
        "dedupe", "people.csv", "", "",   // mode + file
        "", "weighted",                    // mk_1, weighted
        "last_name", "", "",               // field, suggested transforms, suggested scorer
        "1.0",                             // weight
        "",                                // another field? no
        "0.9",                             // threshold
        "",                                // another matchkey? no
        "zip",                             // blocking fields (NOT asked whether to configure)
        "",                                // another blocking key? no
        "", "", "",                        // format, dir, run name
      ]),
    );
    const mk = cfg.matchkeys[0]!;
    expect(mk["type"]).toBe("weighted");
    expect(mk["threshold"]).toBe(0.9);
    expect((mk["fields"] as Array<Record<string, unknown>>)[0]).toEqual({
      field: "last_name",
      transforms: ["lowercase", "strip", "normalize_whitespace"],
      scorer: "jaro_winkler",
      weight: 1.0,
    });
    expect(cfg.blocking).toEqual({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: ["strip", "substring:0:5"] }],
    });
  });

  it("declining the suggested scorer prompts for an explicit one", async () => {
    const cfg = await runWizard(
      scriptedAsk([
        "dedupe", "p.csv", "", "",
        "", "weighted",
        "nickname", "",          // field, suggested transforms yes
        "n", "token_sort",       // decline suggested scorer -> pick explicitly
        "2.5", "", "0.8", "",
        "zip", "",
        "", "", "",
      ]),
    );
    const field = (cfg.matchkeys[0]!["fields"] as Array<Record<string, unknown>>)[0]!;
    expect(field["scorer"]).toBe("token_sort");
    expect(field["weight"]).toBe(2.5);
  });
});

describe("toYaml", () => {
  it("round-trips the wizard shape with key order preserved", () => {
    const yaml = toYaml({
      matchkeys: [{ name: "mk_1", type: "exact", fields: [{ field: "email", transforms: ["lowercase"] }] }],
      output: { format: "csv", directory: "./output" },
    });
    expect(yaml).toContain("matchkeys:");
    expect(yaml).toContain("- name: mk_1");
    expect(yaml.indexOf("matchkeys:")).toBeLessThan(yaml.indexOf("output:"));
    expect(yaml).toContain("- lowercase");
  });

  it("quotes values YAML would otherwise reinterpret", () => {
    // `no`/`yes`/`true` are YAML booleans; a bare digit-leading string becomes a
    // number. A column literally named "no" must survive the round trip as a string.
    expect(toYaml({ a: "no" })).toBe('a: "no"\n');
    expect(toYaml({ a: "true" })).toBe('a: "true"\n');
    expect(toYaml({ a: "0.85" })).toBe('a: "0.85"\n');
    expect(toYaml({ a: 0.85 })).toBe("a: 0.85\n");
    expect(toYaml({ a: "plain" })).toBe("a: plain\n");
  });
});
