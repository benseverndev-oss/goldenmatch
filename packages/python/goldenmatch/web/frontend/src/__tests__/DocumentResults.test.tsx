import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocumentResults } from "../components/DocumentResults";
import type { IngestResult } from "../lib/api";

const result: IngestResult = {
  records: [{ full_name: "Ada", email: "a@x.io", _extract_confidence: 0.9,
              _source_file: "a.png", _source_page: 0 }],
  report: { n_files: 1, n_rows: 1, errors: [] },
};

describe("DocumentResults", () => {
  it("shows records + summary; table omits _source_* columns", () => {
    render(<DocumentResults result={result} schemaColumns={["full_name", "email"]} />);
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText(/1 record/i)).toBeInTheDocument();     // summary
    // table shows _extract_confidence but not _source_file
    expect(screen.queryByText("_source_file")).not.toBeInTheDocument();
  });
});
