import { describe, it, expect } from "vitest";
import { router } from "../router";

describe("router", () => {
  it("registers /documents", () => {
    const paths = Object.keys(router.routesByPath);
    expect(paths).toContain("/documents");
  });
});
