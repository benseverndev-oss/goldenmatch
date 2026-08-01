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

  it("identity360 hits the /360 endpoint and parses the page", async () => {
    const page = {
      entity_id: "E1",
      record_count: 2,
      golden_record: { name: "Alice" },
      field_provenance: [],
      source_records: [],
      timeline: [],
      relationships: [],
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(page),
    });
    vi.stubGlobal("fetch", fetchMock);
    const out = await api.identity360("E1 /x");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/identities/E1%20%2Fx/360");
    expect(out.entity_id).toBe("E1");
    expect(out.record_count).toBe(2);
  });
});
