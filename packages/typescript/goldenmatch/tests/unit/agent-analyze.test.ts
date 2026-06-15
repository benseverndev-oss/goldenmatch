import { describe, it, expect } from "vitest";
import { AgentSession } from "../../src/core/agent/session.js";

describe("AgentSession.analyze", () => {
  it("returns the reasoning dict with rounded profile fields", () => {
    const session = new AgentSession();
    const rows = [
      { id: "1", name: "Alice", note: null },
      { id: "2", name: "Alice", note: "x" },
      { id: "3", name: "Bob", note: "yy" },
      { id: "4", name: "Carol", note: "zzz" },
    ];
    const r = session.analyze(rows);

    // Top-level keys present.
    expect(r.profile).toBeDefined();
    expect(r.strategy).toBeDefined();
    expect(r.why).toBeDefined();
    expect(Array.isArray(r.alternatives)).toBe(true);
    expect(r.profile.row_count).toBe(4);
    expect(r.profile.has_sensitive).toBe(false);

    // uniqueness/null_rate rounded to 4dp; avg_length to 1dp.
    const name = r.profile.fields.find((f) => f.name === "name")!;
    expect(name.uniqueness).toBe(0.75); // 3/4
    expect(name.type).toBe("string");
    // avg_length of "Alice"(5),"Alice"(5),"Bob"(3),"Carol"(5) = 18/4 = 4.5
    expect(name.avg_length).toBe(4.5);

    const note = r.profile.fields.find((f) => f.name === "note")!;
    expect(note.null_rate).toBe(0.25); // 1/4
  });

  it("rounds uniqueness to 4 decimal places", () => {
    const session = new AgentSession();
    // 3 rows -> uniqueness 1/3 = 0.3333...
    const rows = [{ v: "a" }, { v: "a" }, { v: "b" }];
    const r = session.analyze(rows);
    const v = r.profile.fields.find((f) => f.name === "v")!;
    expect(v.uniqueness).toBe(0.6667); // 2/3 rounded to 4dp
  });

  it("stashes reasoning on the session", () => {
    const session = new AgentSession();
    const r = session.analyze([{ id: "1" }]);
    expect(session.reasoning).toBe(r);
  });

  it("flags sensitive data and selects pprl", () => {
    const session = new AgentSession();
    const r = session.analyze([{ ssn: "111-22-3333" }]);
    expect(r.profile.has_sensitive).toBe(true);
    expect(r.strategy).toBe("pprl");
    expect(r.auto_execute).toBe(false);
  });
});
