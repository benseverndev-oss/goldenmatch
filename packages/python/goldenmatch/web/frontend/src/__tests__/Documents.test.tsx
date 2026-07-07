import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Documents } from "../routes/Documents";

vi.mock("../lib/api", () => ({
  api: {
    suggestSchema: vi.fn().mockResolvedValue({ schema: { fields: [
      { name: "full_name", kind: "text", hint: null }] } }),
    ingestDocuments: vi.fn().mockResolvedValue({
      records: [{ full_name: "Ada", _extract_confidence: 0.9 }],
      report: { n_files: 1, n_rows: 1, errors: [] } }),
  },
}));

const wrap = (ui: React.ReactNode) =>
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

describe("Documents route", () => {
  it("upload -> suggest -> extract -> results", async () => {
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /extract/i }));
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());
  });
});
