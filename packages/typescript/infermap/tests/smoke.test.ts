import { describe, it, expect } from "vitest";
import * as infermap from "../src/index.js";

describe("infermap package", () => {
  it("exports a module", () => {
    expect(infermap).toBeTypeOf("object");
  });
});
