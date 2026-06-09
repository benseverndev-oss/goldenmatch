import { describe, expect, it } from "vitest";
import { VERSION } from "../../src/index.js";

describe("smoke", () => {
  it("exports a version", () => {
    expect(VERSION).toBe("0.1.0");
  });
});
