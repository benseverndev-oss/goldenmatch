/**
 * Python parity: the select_strategy decision table is the AgentSession
 * keystone. Goldens emitted by packages/python/goldenmatch/scripts/
 * emit_agent_fixtures.py. Structural assertions (deterministic branching).
 *
 * domain_extraction is intentionally not in the cross-language fixture: Python's
 * domain_confidence is hits/len(signals) while the TS uses detectDomain().
 * confidence (= score/10). That branch is covered by the unit test only.
 */
import { describe, it, expect } from "vitest";
import { profileForAgent, selectStrategy } from "../../src/core/agent/strategy.js";
import type { DataProfile } from "../../src/core/agent/types.js";
import type { Row } from "../../src/core/types.js";
import fixture from "./fixtures/agent-decisions.json" with { type: "json" };

interface Decision {
  strategy: string;
  strong_ids: string[];
  fuzzy_fields: string[];
  backend: string | null;
  auto_execute: boolean;
  domain: string | null;
}
interface RowsCase { name: string; rows: Record<string, string>[]; analyze: Decision }
interface ProfileCase { name: string; profile: unknown; decision: Decision }

const fx = fixture as unknown as {
  rows_cases: RowsCase[];
  profile_cases: ProfileCase[];
};

function assertDecision(actual: Decision, expected: Decision): void {
  expect(actual.strategy).toBe(expected.strategy);
  expect(actual.strong_ids).toEqual(expected.strong_ids);
  expect(actual.fuzzy_fields).toEqual(expected.fuzzy_fields);
  expect(actual.backend).toBe(expected.backend);
  expect(actual.auto_execute).toBe(expected.auto_execute);
  // NOTE: `domain` is intentionally NOT asserted. The TS and Python domain
  // registries name the same domain differently (TS "person" vs Python
  // "people"), an incidental registry-label divergence -- the strategy
  // decision (driven by strong/fuzzy fields) is what matters and matches.
}

describe("agent selectStrategy parity (Python goldens)", () => {
  for (const c of fx.rows_cases) {
    it(`rows: ${c.name}`, () => {
      const decision = selectStrategy(profileForAgent(c.rows as unknown as readonly Row[]));
      assertDecision(decision as unknown as Decision, c.analyze);
    });
  }
  for (const c of fx.profile_cases) {
    it(`profile: ${c.name}`, () => {
      const decision = selectStrategy(c.profile as unknown as DataProfile);
      assertDecision(decision as unknown as Decision, c.decision);
    });
  }
});
