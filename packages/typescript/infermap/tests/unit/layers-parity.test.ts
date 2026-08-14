// Cross-language parity for identity-layer detection.
//
// The SINGLE committed oracle `layers_parity.json` is GENERATED FROM THE RUST
// KERNEL (`infermap-core::detect_identity_layers`) and lives in the Python
// package's fixtures so both surfaces read the same bytes — the
// assignment_parity.json arrangement.
//
// This asserts the pure-TS fallback reproduces the kernel exactly. The kernel is
// the source of truth; pure TS is a classified fallback, so any divergence here
// is a TS bug, not a disagreement between equals.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { computeLayers } from "../../src/core/layers.js";
import type { LayerRoleInput } from "../../src/core/wasm/backend.js";

const FIXTURE = fileURLToPath(
  new URL(
    "../../../../python/infermap/tests/fixtures/layers_parity.json",
    import.meta.url,
  ),
);

interface Case {
  name: string;
  domain: string | null;
  columns: string[];
  roles: LayerRoleInput[];
  type_hints: string[];
  min_score: number;
  expected: {
    layers: Array<{
      role: string;
      kind: string;
      columns: string[];
      score: number;
      reason: string;
      qualifier: string;
      positions: string[];
      role_matched: boolean;
      type_corroboration: number;
    }>;
    unassigned: string[];
  };
}

const cases: Case[] = JSON.parse(readFileSync(FIXTURE, "utf-8"));

describe("identity-layer detection parity (pure TS == Rust kernel)", () => {
  it("has a non-trivial oracle", () => {
    // Guard against an empty/truncated fixture silently passing everything.
    expect(cases.length).toBeGreaterThanOrEqual(10);
  });

  for (const c of cases) {
    it(`matches the kernel: ${c.name}`, () => {
      const got = computeLayers(c.columns, c.roles, c.type_hints, c.min_score);
      // Exact structural equality INCLUDING the unrounded float scores — the
      // whole point of the gate. Rounding is deliberately absent everywhere
      // because Python/Rust/JS round() disagree.
      expect(got.layers).toEqual(c.expected.layers);
      expect(got.unassigned).toEqual(c.expected.unassigned);
    });
  }
});
