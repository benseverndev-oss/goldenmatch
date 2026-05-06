import { describe, it, expect, vi } from "vitest";
import { api } from "../lib/api";

describe("api", () => {
  it("throws on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve("boom"),
      }),
    );
    await expect(api.project()).rejects.toThrow(/500/);
  });
});
