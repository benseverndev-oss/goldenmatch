import { describe, it, expect } from "vitest";
import { RUNNER_VERSION, MANIFEST_VERSION, REPORT_VERSION } from "../src/index.js";

describe("@infermap/bench smoke", () => {
  it("exports version constants", () => {
    expect(RUNNER_VERSION).toBe("0.1.0");
    expect(MANIFEST_VERSION).toBe(1);
    expect(REPORT_VERSION).toBe(1);
  });
});
