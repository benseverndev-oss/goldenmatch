/**
 * Python parity for AgentSession.analyze() reasoning: profile field numerics
 * (4-decimal uniqueness/null_rate, 1-decimal avg_length) + the decision fields.
 * Goldens from emit_agent_fixtures.py (no-null datasets, so polars n_unique
 * matches the TS distinct-over-non-nulls count).
 */
import { describe, it, expect } from "vitest";
import { AgentSession } from "../../src/core/agent/session.js";
import type { Row } from "../../src/core/types.js";
import fixture from "./fixtures/agent-decisions.json" with { type: "json" };

interface GoldenField {
  name: string;
  type: string;
  uniqueness: number;
  null_rate: number;
  avg_length: number;
}
interface GoldenAnalyze {
  profile: { row_count: number; fields: GoldenField[]; has_sensitive: boolean };
  strategy: string;
  strong_ids: string[];
  fuzzy_fields: string[];
  backend: string | null;
  auto_execute: boolean;
  domain: string | null;
}
interface RowsCase { name: string; rows: Record<string, string>[]; analyze: GoldenAnalyze }

const fx = fixture as unknown as { rows_cases: RowsCase[] };

describe("agent analyze() parity (Python goldens)", () => {
  for (const c of fx.rows_cases) {
    it(`analyze: ${c.name}`, () => {
      const out = new AgentSession().analyze(c.rows as unknown as readonly Row[]);
      const g = c.analyze;

      expect(out.profile.row_count).toBe(g.profile.row_count);
      expect(out.profile.has_sensitive).toBe(g.profile.has_sensitive);
      expect(out.profile.fields.length).toBe(g.profile.fields.length);
      out.profile.fields.forEach((f, i) => {
        const gf = g.profile.fields[i]!;
        expect(f.name).toBe(gf.name);
        expect(f.type).toBe(gf.type);
        expect(f.uniqueness).toBeCloseTo(gf.uniqueness, 4);
        expect(f.null_rate).toBeCloseTo(gf.null_rate, 4);
        expect(f.avg_length).toBeCloseTo(gf.avg_length, 1);
      });

      expect(out.strategy).toBe(g.strategy);
      expect(out.strong_ids).toEqual(g.strong_ids);
      expect(out.fuzzy_fields).toEqual(g.fuzzy_fields);
      expect(out.backend).toBe(g.backend);
      expect(out.auto_execute).toBe(g.auto_execute);
      expect(out.domain).toBe(g.domain);
    });
  }
});
