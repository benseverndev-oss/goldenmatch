import type { ReactNode } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PairFieldBreakdown } from "../components/PairFieldBreakdown";
import type { Pair } from "../lib/types";

const fixturePair: Pair = {
  row_id_a: 0,
  row_id_b: 1,
  score: 0.9,
  cluster_id: 1,
  fields: [
    {
      field: "name",
      scorer: "jaro_winkler",
      value_a: "Sony DSC-T77 Silver",
      value_b: "Sony DSC-T77 Black",
      score: 0.9,
      weight: 1.0,
      diff_type: "different",
    },
  ],
};

const wrap = (ui: ReactNode) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);

describe("PairFieldBreakdown", () => {
  it("renders score and pair ids", () => {
    render(wrap(<PairFieldBreakdown pair={fixturePair} />));
    expect(screen.getByText(/0 → 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/0\.900/).length).toBeGreaterThan(0);
    expect(screen.getByText(/jaro_winkler/)).toBeInTheDocument();
    expect(screen.getByText(/Sony DSC-T77 Silver/)).toBeInTheDocument();
  });

  it("posts a label when 'Label match' is clicked", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(wrap(<PairFieldBreakdown pair={fixturePair} />));
    fireEvent.click(screen.getByRole("button", { name: /label match/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/labels");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({
      row_id_a: 0,
      row_id_b: 1,
      label: "match",
    });
  });
});
