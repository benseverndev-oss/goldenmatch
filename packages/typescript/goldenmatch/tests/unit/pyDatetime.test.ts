/**
 * pyIsoformat parity tests. Asserts the helper reproduces Python
 * datetime.isoformat() for UTC datetimes against a committed fixture:
 * microseconds omitted when zero, 6-digit microseconds otherwise (JS
 * milliseconds padded to microseconds), no `Z`/offset suffix.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { describe, it, expect } from "vitest";

import { pyIsoformat } from "../../src/core/identity/pyDatetime.js";

const HERE = dirname(fileURLToPath(import.meta.url));

interface Fixture {
  cases: { iso: string; expected: string }[];
}

const fixture = JSON.parse(
  readFileSync(join(HERE, "fixtures", "py-isoformat.json"), "utf8"),
) as Fixture;

describe("pyIsoformat", () => {
  it("matches the committed Python-isoformat fixture", () => {
    for (const c of fixture.cases) {
      expect(pyIsoformat(new Date(c.iso))).toBe(c.expected);
    }
  });

  it("omits the fractional part entirely when microseconds are zero", () => {
    expect(pyIsoformat(new Date("2026-01-02T03:04:05.000Z"))).toBe("2026-01-02T03:04:05");
  });

  it("pads JS milliseconds to 6-digit microseconds when non-zero", () => {
    // 678 ms -> 678000 microseconds (ms * 1000).
    expect(pyIsoformat(new Date("2026-01-02T03:04:05.678Z"))).toBe(
      "2026-01-02T03:04:05.678000",
    );
    // 7 ms -> 007000 microseconds (leading-zero padding on both sides).
    expect(pyIsoformat(new Date("2026-01-02T03:04:05.007Z"))).toBe(
      "2026-01-02T03:04:05.007000",
    );
  });

  it("emits no Z / offset suffix", () => {
    const out = pyIsoformat(new Date("2026-01-02T03:04:05.500Z"));
    expect(out.endsWith("Z")).toBe(false);
    expect(out).toBe("2026-01-02T03:04:05.500000");
  });

  it("zero-pads month/day/hour/minute/second to two digits", () => {
    expect(pyIsoformat(new Date("2026-03-04T05:06:07.000Z"))).toBe("2026-03-04T05:06:07");
  });
});
