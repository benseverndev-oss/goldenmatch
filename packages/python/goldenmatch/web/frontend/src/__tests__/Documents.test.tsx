import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Documents } from "../routes/Documents";
import { api } from "../lib/api";
import { humanizeError } from "../lib/errors";

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

const selectFile = (input: HTMLInputElement, file: File) =>
  fireEvent.change(input, { target: { files: [file] } });

describe("Documents route", () => {
  beforeEach(() => {
    vi.mocked(api.suggestSchema).mockClear();
    vi.mocked(api.ingestDocuments).mockClear();
  });

  it("upload -> suggest -> extract -> results", async () => {
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    selectFile(input, file);
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /extract/i }));
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());
  });

  it("disables Extract and does not call ingestDocuments when files are cleared after suggesting a schema", async () => {
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    selectFile(input, file);
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());

    // Clear the file input.
    fireEvent.change(input, { target: { files: [] } });

    const extractBtn = screen.getByRole("button", { name: /extract/i });
    expect(extractBtn).toBeDisabled();
    fireEvent.click(extractBtn);
    expect(api.ingestDocuments).not.toHaveBeenCalled();
  });

  it("disables Extract when the schema has no fields", async () => {
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    selectFile(input, file);
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /remove/i }));

    const extractBtn = screen.getByRole("button", { name: /extract/i });
    expect(extractBtn).toBeDisabled();
    fireEvent.click(extractBtn);
    expect(api.ingestDocuments).not.toHaveBeenCalled();
  });

  it("clears stale results when the schema is edited after an extract", async () => {
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    selectFile(input, file);
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() => expect(screen.getByDisplayValue("full_name")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /extract/i }));
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("full_name"), {
      target: { value: "full_name_edited" },
    });

    await waitFor(() => expect(screen.queryByText("Ada")).not.toBeInTheDocument());
  });

  it("renders the detail message from a JSON error body", async () => {
    vi.mocked(api.suggestSchema).mockRejectedValueOnce(
      new Error('400 {"detail":"invalid schema: missing fields"}'),
    );
    render(wrap(<Documents />));
    const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const input = screen.getByLabelText(/upload|files/i) as HTMLInputElement;
    selectFile(input, file);
    fireEvent.click(screen.getByRole("button", { name: /suggest/i }));
    await waitFor(() =>
      expect(screen.getByText(/invalid schema: missing fields/)).toBeInTheDocument(),
    );
  });
});

describe("humanizeError", () => {
  it("unwraps the detail field from a JSON error body", () => {
    expect(humanizeError('400 {"detail":"invalid schema: missing fields"}')).toBe(
      "invalid schema: missing fields",
    );
  });

  it("returns the original message when it isn't the status+JSON shape", () => {
    expect(humanizeError("Network error")).toBe("Network error");
  });

  it("returns the original message when the JSON body has no string detail", () => {
    expect(humanizeError('400 {"foo":"bar"}')).toBe('400 {"foo":"bar"}');
  });
});
